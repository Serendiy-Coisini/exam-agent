"""Request scopes and public qualification evidence survive analyst boundaries."""
import unittest
from unittest.mock import patch

from app.agents import exam_agent
from app.schemas.agent_schema import CurrentJobContext, MatchedJob
from app.services.analyst_result_service import qualify_jobs
from app.tools import job_tool, region_resolver
from tests import test_analyst_repair_integration as integration


class AnalystEvidenceRoundtripTests(unittest.TestCase):
    def setUp(self):
        self.fixture = integration.AnalystRepairIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_planned_destination_is_not_overridden_by_hukou_background(self):
        payload = self.fixture.payload("户籍是广西南宁，按画像推荐")
        rows = [self.fixture.job("111111"), self.fixture.job(
            "222222", region="广西", province="广西", city="南宁", department="南宁市税务局")]
        aliases = [dict(alias="南宁", normalized_alias="南宁", province="广西",
                        city="南宁", district="", level="city")]
        with patch.object(region_resolver, "_imported_city_aliases", return_value=aliases):
            response = self.fixture.run_jobs(payload, rows)
        self.assertEqual([(job.position_code, job.region, job.city) for job in response.matched_jobs],
                         [("111111", "广东", "揭阳")])
        self.assertEqual(response.analysis_metadata["effective_conditions"]["city"], "揭阳")

    def test_standalone_search_still_accepts_question_location(self):
        rows = [self.fixture.job("111111"), self.fixture.job(
            "222222", region="广西", province="广西", city="南宁", department="南宁市税务局")]
        aliases = [dict(alias="南宁", normalized_alias="南宁", province="广西",
                        city="南宁", district="", level="city")]
        with patch.object(job_tool, "_read_jobs", return_value=rows), patch.object(
                region_resolver, "_imported_city_aliases", return_value=aliases):
            result = job_tool.search_jobs(target="公务员", exam_type="国考", region="广东",
                city="揭阳", education="本科", major="软件工程", identity="应届生",
                question="推荐广西南宁税务岗", year=2027)
        self.assertEqual([job["position_code"] for job in result], ["222222"])

    def test_public_extra_requirements_survive_response_and_context_schemas(self):
        payload = self.fixture.payload("这个岗位能不能报", profile={"gender":"男"})
        original = self.fixture.job("333333", eligibility_extra_requirements="性别：女性")
        qualified = qualify_jobs(payload, [original])[0]
        self.assertEqual(qualified["eligibility_status"], "unmet")
        response_job = MatchedJob(**exam_agent._prepare_job_response_fields(payload, [qualified])[0]).model_dump()
        context = CurrentJobContext(position_code="333333", education_requirement="本科",
            major_requirement="软件工程", identity_requirement="不限",
            eligibility_extra_requirements=response_job.get("eligibility_extra_requirements", "")).model_dump()
        record = dict(context, education_required=context["education_requirement"],
                      major_required=context["major_requirement"], identity_required=context["identity_requirement"])
        rechecked = qualify_jobs(payload, [record])[0]
        self.assertEqual(rechecked["eligibility_status"], "unmet")
        self.assertTrue(any(check["field"] == "性别" and check["status"] == "unmet"
                            for check in rechecked["eligibility_checks"]))

    def lookup(self, context, rows):
        lookup = getattr(job_tool, "find_job_by_identity", None)
        self.assertTrue(callable(lookup), "Current cards need a server evidence lookup by full identity")
        with patch.object(job_tool, "_read_jobs", return_value=rows):
            return lookup(context)

    def test_current_job_lookup_restores_server_evidence_for_exact_identity(self):
        correct = self.fixture.job("333333", department_code="A", city="汕头",
                                   eligibility_extra_requirements="性别：女性")
        rows = [self.fixture.job("333333", department_code="B"),
                self.fixture.job("333333", department_code="A", year=2026),
                self.fixture.job("333333", department_code="A", exam_type="省考"),
                self.fixture.job("333333", department_code="A", region="广西", province="广西"), correct]
        context = dict(position_code="333333", department_code="A", year=2027,
                       exam_type="国考", province="广东", city="揭阳",
                       eligibility_extra_requirements="性别：男性")
        result = self.lookup(context, rows)
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["job"]["city"], "汕头")
        self.assertEqual(result["job"]["eligibility_extra_requirements"], "性别：女性")

    def test_current_job_lookup_refuses_ambiguous_department(self):
        rows = [self.fixture.job("333333", department_code="A"),
                self.fixture.job("333333", department_code="B")]
        result = self.lookup(dict(position_code="333333", year=2027, exam_type="国考", province="广东"), rows)
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["job"])


if __name__ == "__main__":
    unittest.main()
