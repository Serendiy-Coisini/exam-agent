"""Category requests must constrain actual candidates, including without a model."""
import asyncio
import unittest
from unittest.mock import patch

from app.agents import exam_agent, intent_router, prompt_builder
from app.schemas.agent_schema import AgentAnalyzeRequest
from app.services import model_settings_service
from app.tools import job_tool
from tests import test_regressions as fixtures


class JobCategoryTests(unittest.TestCase):
    def job(self, code, department, title="一级行政执法员", **changes):
        job = dict(job_id=code, position_code=code, target="公务员", exam_type="国考",
                    region="广东", city="揭阳", year=2027, department=department, unit=department,
                    position=title, position_name=title, education_required="本科",
                    major_required="软件工程", identity_required="不限", headcount=2)
        job.update(changes)
        return job

    def search(self, question, jobs):
        with patch.object(job_tool, "_read_jobs", return_value=jobs), \
                patch.object(job_tool, "list_imported_major_catalog", return_value=[]):
            return job_tool.search_jobs(target="公务员", exam_type="国考", region="广东",
                city="揭阳", education="本科", major="软件工程", identity="应届生",
                question=question, year=2027, limit=5)

    def test_police_request_excludes_tax_even_with_police_words_in_remarks(self):
        rows = self.search("有没有揭阳公安岗的", [
            self.job("TAX", "国家税务总局揭西县税务局", notes="非公安岗；配合公安工作", remark="参照人民警察体检"),
            self.job("POLICE", "揭阳市公安局", "执法勤务民警"),
            self.job("GENERIC", "揭阳市市场监督管理局")])
        self.assertEqual([j["position_code"] for j in rows], ["POLICE"])

    def test_no_police_match_does_not_pad_tax_or_other_city(self):
        self.assertEqual(self.search("有没有揭阳公安岗的", [
            self.job("TAX", "揭西县税务局"), self.job("FAR", "汕头市公安局", city="汕头")]), [])

    def test_category_keeps_major_qualification_checks(self):
        jobs=[self.job("LAW", "揭阳市公安局", major_required="法学类"),
              self.job("TAX", "揭西县税务局")]
        self.assertEqual(self.search("找揭阳公安岗",jobs),[])

    def test_railway_police_smuggling_and_border_police_are_not_generic_customs(self):
        jobs=[self.job("RAIL", "广州铁路公安局", "基层所队民警"),
              self.job("SMUGGLE", "汕头海关缉私局", "一级警长"),
              self.job("BORDER", "揭阳出入境边防检查站", "执勤民警"),
              self.job("CUSTOM", "揭阳海关", "海关业务管理")]
        self.assertEqual({j["position_code"] for j in self.search("有没有揭阳公安岗的",jobs)},
                         {"RAIL","SMUGGLE","BORDER"})

    def test_category_switch_union_exclusion_and_no_constraint(self):
        jobs=[self.job("POLICE","揭阳市公安局"),self.job("TAX","揭西县税务局")]
        for question,expected in [("推荐公安或者税务岗",{"POLICE","TAX"}),
                                  ("不要公安，只看税务岗",{"TAX"}),
                                  ("有没有税务岗",{"TAX"}),
                                  ("按我的画像推荐岗位",{"POLICE","TAX"})]:
            self.assertEqual({j["position_code"] for j in self.search(question,jobs)},expected,question)

    def test_existence_question_routes_without_model_but_risk_and_policy_stay_separate(self):
        for question in ("有没有揭阳公安岗的","有无揭阳税务岗位","是否有海关岗位"):
            self.assertEqual(intent_router.route_intent(question,has_current_job=True),"job_recommendation")
            payload=AgentAnalyzeRequest(question=question,region="广东",city="揭阳",education="本科",
                major="软件工程",identity="应届生",exam_type="国考")
            with patch.object(exam_agent,"generate_llm_json",side_effect=AssertionError("No model planning needed")):
                self.assertEqual(exam_agent._decide_llm_first(payload,"")["intent"],"job_recommendation")
        self.assertEqual(intent_router.route_intent("公安岗有什么风险"),"selection_strategy")

    def test_negation_and_previous_category_do_not_hide_or_expand_current_request(self):
        jobs=[self.job("POLICE","揭阳市公安局"),self.job("TAX","揭西县税务局")]
        for question in ("有没有非公安的税务岗", "公安没有合适的，推荐税务岗",
                         "我之前看过公安岗，有没有税务岗", "之前看过公安岗现在只看税务岗"):
            self.assertEqual({j["position_code"] for j in self.search(question,jobs)},{"TAX"},question)
        for question in ("不要公安和税务岗", "不要公安或者税务岗", "不要公安与税务岗", "不要公安以及税务岗"):
            self.assertEqual(self.search(question,jobs),[],question)

    def test_model_context_carries_category_constraint(self):
        payload=AgentAnalyzeRequest(question="有没有揭阳公安岗的",city="揭阳")
        context=prompt_builder.build_current_profile_context(payload)
        self.assertEqual(context["岗位类别约束"],{"include":["公安"],"exclude":[]})


class CategoryApiTests(unittest.TestCase):
    setUp=fixtures.AuditRegressions.setUp
    tearDown=fixtures.AuditRegressions.tearDown
    row=fixtures.AuditRegressions.row
    task=fixtures.AuditRegressions.task

    def test_api_no_matches_reports_category_without_recommending_old_tax_cards(self):
        from app.services.import_service import confirm_import_task
        self.task([self.row(province="guangdong",region="广东省揭阳市",year=2027,exam_type="国考")])
        confirm_import_task("import_fixture")
        jobs=[JobCategoryTests().job("TAX","国家税务总局揭西县税务局")]
        configuration=model_settings_service.ModelConfiguration("https://fiction.invalid","fiction","")
        with patch.object(job_tool,"_read_jobs",return_value=jobs), \
                patch.object(job_tool,"list_imported_major_catalog",return_value=[]), \
                patch("app.services.llm_service.get_model_configuration",return_value=configuration):
            status,result=asyncio.run(fixtures.request("POST","/api/v1/agent/analyze",{
                "question":"有没有揭阳公安岗的","region":"广东","city":"揭阳","education":"本科",
                "major":"软件工程","identity":"应届生","exam_type":"国考","year":2027,
                "current_job":{"position_code":"TAX","department":"揭西县税务局"},
                "last_recommendations":[{"position_code":"TAX","department":"揭西县税务局"}],
                "messages":[{"role":"assistant","content":"推荐揭西县税务局岗位"}]}))
        self.assertEqual(status,200)
        self.assertEqual(result["intent"],"job_recommendation")
        self.assertEqual(result["matched_jobs"],[])
        self.assertEqual(result["recommendations"],[])
        self.assertIn("揭阳",result["analysis_report"])
        self.assertIn("公安",result["analysis_report"])
        self.assertIn("本次",result["analysis_report"])
        self.assertNotIn("推荐揭西县税务局",result["analysis_report"])
        self.assertFalse(result["llm_used"])
