"""City constraints must survive profile reuse and recommendation ranking."""
import asyncio
import unittest
from unittest.mock import patch

from app.agents import exam_agent
from app.schemas.agent_schema import AgentAnalyzeRequest
from app.services.product_service import apply_profile_defaults
from app.tools import job_tool, region_resolver
from tests import test_regressions as fixtures
request=fixtures.request


class AnalystCityScopeTests(unittest.TestCase):
    def job(self, code, city, **changes):
        return {"job_id":code, "position_code":code, "target":"公务员", "exam_type":"国考",
                "region":"广东", "city":city, "year":2027, "department":"虚构单位",
                "unit":"虚构单位", "position_name":"软件技术岗位", "education_required":"本科",
                "major_required":"软件工程", "identity_required":"不限", "headcount":2, **changes}

    def search(self, jobs, **changes):
        args = dict(target="公务员",exam_type="国考",region="广东",city="汕头",education="本科",
                    major="软件工程",identity="应届生",question="按我的画像推荐一个岗位",limit=5,year=2027)
        args.update(changes)
        with patch.object(job_tool,"_read_jobs",return_value=jobs), \
                patch.object(job_tool,"list_imported_major_catalog",return_value=[]):
            return job_tool.search_jobs(**args)

    def test_city_is_a_hard_scope_and_never_pads_other_cities(self):
        rows=self.search([self.job("LOCAL","广东省汕头"), self.job("FAR","广东省湛江",headcount=10)])
        self.assertEqual([r["position_code"] for r in rows],["LOCAL"])

    def test_no_local_qualified_job_returns_empty_instead_of_relaxing_city(self):
        rows=self.search([self.job("LOCAL","汕头",education_required="博士"), self.job("FAR","湛江")])
        self.assertEqual(rows,[])

    def test_department_name_and_unknown_location_do_not_prove_target_city(self):
        rows=self.search([self.job("OTHER","广东省汕尾",department="汕头海关"),
                          self.job("UNKNOWN","",work_location="广东",department="汕头海关"),
                          self.job("MULTI","汕头、湛江")])
        self.assertEqual(rows,[])

    def test_city_alias_full_address_and_new_profile_city_are_respected(self):
        jobs=[self.job("ST","广东省汕头",work_location="广东省汕头市澄海区"),self.job("ZJ","广东省湛江")]
        self.assertEqual([r["position_code"] for r in self.search(jobs,city="汕头市")],["ST"])
        self.assertEqual([r["position_code"] for r in self.search(jobs,city="湛江")],["ZJ"])
        self.assertEqual(len(self.search(jobs,city="不限")),2)

    def test_missing_city_message_names_the_constraint_and_does_not_suggest_broadening_major(self):
        payload=AgentAnalyzeRequest(question="按画像推荐",region="广东",city="汕头")
        message=exam_agent._build_no_jobs_fallback_reason(payload)
        self.assertIn("汕头",message)
        self.assertNotIn("放宽城市、专业",message)
        self.assertIn("未自动扩大",message)


    def test_local_and_commuting_preferences_do_not_authorize_nearby_cities(self):
        local=self.job("LOCAL","南宁",region="广西")
        neighbor=self.job("NEIGHBOR","钦州",region="广西")
        for question in ("推荐南宁本地岗位","我家在南宁，想通勤","南宁就近报考","只在南宁，不考虑周边"):
            with self.subTest(question=question):
                self.assertEqual([r["position_code"] for r in self.search([local,neighbor],city="南宁",region="广西",question=question)],["LOCAL"])
        self.assertEqual(len(self.search([local,neighbor],city="南宁",region="广西",question="南宁及周边岗位")),2)


class ImportedCityResolverTests(unittest.TestCase):
    setUp=fixtures.AuditRegressions.setUp
    tearDown=fixtures.AuditRegressions.tearDown
    row=fixtures.AuditRegressions.row
    task=fixtures.AuditRegressions.task

    def test_imported_administrative_city_works_without_private_region_csv(self):
        from app.services.import_service import confirm_import_task
        self.task([self.row(province="guangdong",region="广东省汕头市",year=2027,exam_type="国考")])
        confirm_import_task("import_fixture")
        with patch.object(region_resolver,"_load_region_aliases",return_value=()):
            result=region_resolver.resolve_region("请推荐汕头的岗位")
        self.assertEqual(result["city"],"汕头")
        self.assertEqual(result["province"],"广东")

    def test_anonymous_request_can_inherit_explicit_request_profile_city(self):
        captured=[]
        original=exam_agent.analyze_exam_request
        def inspect(payload):
            captured.append(payload)
            from app.schemas.agent_schema import AgentAnalyzeResponse
            return AgentAnalyzeResponse(intent="job_recommendation",summary="虚构验证",analysis_report="虚构验证")
        with patch("app.api.v1.agent.analyze_exam_request",side_effect=inspect):
            status,_=asyncio.run(request("POST","/api/v1/agent/analyze",{
                "question":"按我的画像推荐一个岗位", "profile":{"province":"广东","city":"汕头","major":"软件工程"}}))
        self.assertEqual(status,200)
        self.assertEqual(captured[0].city,"汕头")


    def test_adjusted_city_survives_api_profile_merge_and_old_job_context(self):
        from app.api.v1 import agent as api
        from app.agents import prompt_builder
        from app.services import model_settings_service
        captured = []
        saved = {"province": "广东", "city": "汕头", "education": "本科",
                 "major": "软件工程", "identity": "应届生", "exam_type": "国考"}
        configuration = model_settings_service.ModelConfiguration("https://fiction.invalid", "fiction", "")
        original = api.analyze_exam_request
        def inspect(payload):
            captured.append(prompt_builder.build_current_profile_context(payload))
            return original(payload)
        from app.services.import_service import confirm_import_task
        self.task([self.row(province="guangdong", region="广东省汕头市", year=2027, exam_type="国考")])
        confirm_import_task("import_fixture")
        jobs = [AnalystCityScopeTests().job("ST", "汕头"),
                AnalystCityScopeTests().job("JY", "揭阳")]
        for job in jobs:
            job["position"] = job["position_name"]
        with patch.object(api, "get_optional_agent_user", return_value={"id": 1}), \
                patch.object(api.repositories, "get_user_profile", return_value=saved), \
                patch.object(api, "analyze_exam_request", side_effect=inspect), \
                patch.object(exam_agent, "_decide_llm_first", return_value={"intent": "job_recommendation"}), \
                patch.object(job_tool, "_read_jobs", return_value=jobs), \
                patch.object(job_tool, "list_imported_major_catalog", return_value=[]), \
                patch("app.services.llm_service.get_model_configuration", return_value=configuration):
            for city, expected in (("揭阳", {"JY"}), ("汕头", {"ST"}), ("不限", {"ST", "JY"})):
                status, result = asyncio.run(request("POST", "/api/v1/agent/analyze", {
                    "mode": "job_recommendation", "question": "按我的画像推荐岗位", "city": city,
                    "region": "广东", "exam_type": "国考", "education": "本科",
                    "major": "软件工程", "identity": "应届生",
                    "year": 2027, "result_limit": 5, "profile": saved,
                    "current_job": {"position_code": "ST", "city": "汕头"},
                    "last_recommendations": [{"position_code": "ST", "city": "汕头"}],
                    "messages": [{"role": "assistant", "content": "此前推荐汕头岗位ST"}]}))
                self.assertEqual(status, 200)
                self.assertEqual({j["position_code"] for j in result["matched_jobs"]}, expected, result)
                self.assertEqual(captured[-1]["目标城市"], city)
                self.assertEqual(captured[-1]["当前画像资格与偏好"]["city"], city)
        self.assertEqual(saved["city"], "汕头")

    def test_imported_multi_city_source_is_not_collapsed_into_one_city(self):
        from app.services.import_service import confirm_import_task
        self.task([self.row(province="guangdong",region="广东省汕头市",year=2027,exam_type="国考"),
                   self.row(province="guangdong",region="广东省湛江市",year=2027,exam_type="国考",job_code="87654321")])
        confirm_import_task("import_fixture")
        raw=self.row(province="guangdong",region="汕头、湛江",year=2027,exam_type="国考")
        converted=job_tool._normalize_imported_job_row(raw)
        preference=job_tool.extract_location_preference("按我的画像推荐",city="汕头",region="广东")
        self.assertFalse(job_tool._matches_recommendation_city(converted,preference))


if __name__=="__main__":
    unittest.main()
