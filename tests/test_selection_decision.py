"""Bounded candidate selection using fictional model responses only."""
import copy
import importlib
import importlib.util
import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app.schemas.agent_schema import AgentAnalyzeRequest


class SelectionDecisionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("app.services.selection_decision_service"),
                             "The candidate selection service has not been implemented")
        self.service = importlib.import_module("app.services.selection_decision_service")
        self.payload = AgentAnalyzeRequest(
            question="按我的画像推荐一个岗位，优先软件开发方向", education="本科",
            major="软件工程", region="广东", city="广州", exam_type="国考", year=2026,
            profile={"major": "法学", "gender": "女", "email": "private@example.invalid"})
        self.jobs = [
            {"job_id": "rule-first", "department_code": "001", "position_code": "1001",
             "year": 2026, "exam_type": "国考", "region": "广东", "city": "广州",
             "position_name": "综合管理", "job_description": "行政综合事务", "recruit_count": 3,
             "eligibility_status": "matched", "eligibility_checks": [], "match_score": 90},
            {"job_id": "software-second", "department_code": "002", "position_code": "1001",
             "year": 2026, "exam_type": "国考", "region": "广东", "city": "广州",
             "position_name": "软件开发", "job_description": "业务系统软件开发", "recruit_count": 2,
             "eligibility_status": "matched", "eligibility_checks": [], "match_score": 80},
        ]

    def response(self, prompt, fallback_data, analysis_system_prompt="", choose=1, **changes):
        data = json.loads(prompt)
        candidate = data["candidates"][choose]
        entry = {
            "candidate_id": candidate["candidate_id"], "recommendation_grade": "优先备选",
            "rationale": "职位简介包含软件开发，符合当前的软件方向偏好。",
            "tradeoff": "招录名额不能替代报名人数和竞争比，仍需核验岗位要求。",
            "evidence_refs": ["job_description", "recruit_count"],
        }
        entry.update(changes)
        return {"ranked_jobs": [entry], "llm_used": True, "model": "fiction-model"}

    def test_model_can_choose_the_second_eligible_job_using_current_preferences(self):
        seen = {}

        def model(prompt, fallback_data, analysis_system_prompt=""):
            seen.update(data=json.loads(prompt), system=analysis_system_prompt)
            return self.response(prompt, fallback_data, analysis_system_prompt)

        original_jobs, original_payload = copy.deepcopy(self.jobs), self.payload.model_dump()
        with patch.object(self.service.llm_service, "generate_llm_json", side_effect=model) as call:
            selected, metadata = self.service.select_candidates(self.payload, self.jobs, 1)
        self.assertEqual([job["job_id"] for job in selected], ["software-second"])
        self.assertEqual(metadata["source"], "model")
        self.assertTrue(metadata["model_used"])
        self.assertTrue(metadata["skill_applied"])
        self.assertTrue(metadata["skill_revision"])
        self.assertEqual(metadata["compared_count"], 2)
        self.assertEqual(metadata["selected_ids"], [selected[0]["selection_decision"]["candidate_id"]])
        self.assertIn("先硬筛，再优化", seen["system"])
        self.assertIn("ranked_jobs", seen["system"])
        self.assertIn("本阶段", seen["system"])
        profile = seen["data"]["current_profile"]["当前画像资格与偏好"]
        self.assertEqual(profile["major"], "软件工程")
        self.assertEqual(profile["gender"], "女")
        self.assertNotIn("email", profile)
        self.assertEqual(seen["data"]["question"], self.payload.question)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(self.jobs, original_jobs)
        self.assertEqual(self.payload.model_dump(), original_payload)
        selected[0]["eligibility_checks"].append({"field": "changed"})
        self.assertEqual(self.jobs, original_jobs)

    def test_same_position_and_reused_job_ids_remain_distinct_across_scopes(self):
        for job in self.jobs:
            job["job_id"] = "reused"
        extra = copy.deepcopy(self.jobs[0])
        extra["year"] = 2025
        provincial = copy.deepcopy(extra)
        provincial["exam_type"] = "省考"
        candidates = [*self.jobs, extra, provincial]
        observed = []

        def model(prompt, fallback_data, analysis_system_prompt=""):
            data = json.loads(prompt)
            observed.extend(row["candidate_id"] for row in data["candidates"])
            return self.response(prompt, fallback_data, choose=1)

        with patch.object(self.service.llm_service, "generate_llm_json", side_effect=model):
            selected, _ = self.service.select_candidates(self.payload, candidates, 1)
        self.assertEqual(len(set(observed)), 4)
        self.assertEqual(selected[0]["department_code"], "002")

    def test_missing_job_ids_use_stable_composite_identity(self):
        for job in self.jobs:
            del job["job_id"]
        calls = []

        def model(prompt, fallback_data, analysis_system_prompt=""):
            data = json.loads(prompt)
            calls.append({row["department_code"]: row["candidate_id"] for row in data["candidates"]})
            return {"llm_used": False, "error": "fiction unavailable"}

        with patch.object(self.service.llm_service, "generate_llm_json", side_effect=model):
            self.service.select_candidates(self.payload, self.jobs, 1)
            self.service.select_candidates(self.payload, list(reversed(self.jobs)), 1)
        self.assertEqual(calls[0], calls[1])
        self.assertNotEqual(calls[0]["001"], calls[0]["002"])

    def test_unavailable_model_preserves_rule_order_and_marks_fallback(self):
        with patch.object(self.service.llm_service, "generate_llm_json",
                          return_value={"llm_used": False, "failure_reason": "not_configured"}):
            selected, metadata = self.service.select_candidates(self.payload, self.jobs, 1)
        self.assertEqual(selected[0]["job_id"], "rule-first")
        self.assertEqual(metadata["source"], "rules")
        self.assertFalse(metadata["model_used"])
        self.assertFalse(metadata["skill_applied"])
        self.assertEqual(metadata["fallback_reason"], "not_configured")
        self.assertEqual(metadata["compared_count"], 0)
        self.assertEqual(metadata["pool_count"], 2)

    def test_unmet_and_duplicates_are_excluded_before_the_model(self):
        blocked = copy.deepcopy(self.jobs[0])
        blocked.update(job_id="blocked", eligibility_status="unmet")
        contradictory = copy.deepcopy(self.jobs[0])
        contradictory.update(job_id="contradictory", eligibility_checks=[{"status": "unmet"}])
        candidates = [blocked, contradictory, self.jobs[0], copy.deepcopy(self.jobs[0]), self.jobs[1]]
        seen = []

        def model(prompt, fallback_data, analysis_system_prompt=""):
            seen.extend(json.loads(prompt)["candidates"])
            return self.response(prompt, fallback_data)

        with patch.object(self.service.llm_service, "generate_llm_json", side_effect=model):
            selected, metadata = self.service.select_candidates(self.payload, candidates, 1)
        self.assertEqual([job["job_id"] for job in seen], ["rule-first", "software-second"])
        self.assertEqual(selected[0]["job_id"], "software-second")
        self.assertEqual(metadata["candidate_count"], 5)
        self.assertEqual(metadata["excluded_unmet_count"], 2)
        self.assertEqual(metadata["duplicate_count"], 1)

    def test_unknown_or_ineligible_external_ids_cause_rule_fallback(self):
        for value in ("outside-pool", "blocked"):
            with self.subTest(candidate_id=value), patch.object(
                    self.service.llm_service, "generate_llm_json",
                    side_effect=lambda *args, **kwargs: self.response(*args, **kwargs, candidate_id=value)):
                selected, metadata = self.service.select_candidates(self.payload, self.jobs, 1)
            self.assertEqual(selected[0]["job_id"], "rule-first")
            self.assertEqual(metadata["fallback_reason"], "invalid_candidate_id")
            self.assertFalse(metadata["model_used"])

    def test_duplicate_ranked_ids_reject_the_whole_model_ranking(self):
        def model(*args, **kwargs):
            result = self.response(*args, **kwargs)
            result["ranked_jobs"].append(copy.deepcopy(result["ranked_jobs"][0]))
            return result

        with patch.object(self.service.llm_service, "generate_llm_json", side_effect=model):
            selected, metadata = self.service.select_candidates(self.payload, self.jobs, 2)
        self.assertEqual([job["job_id"] for job in selected], ["rule-first", "software-second"])
        self.assertEqual(metadata["fallback_reason"], "duplicate_candidate_id")

    def test_missing_or_invalid_decision_evidence_is_not_accepted(self):
        cases = [({"evidence_refs": []}, "invalid_evidence_refs"),
                 ({"evidence_refs": ["salary"]}, "invalid_evidence_refs"),
                 ({"evidence_refs": [7]}, "invalid_evidence_refs"),
                 ({"rationale": "  "}, "invalid_decision_schema"),
                 ({"tradeoff": None}, "invalid_decision_schema"),
                 ({"recommendation_grade": "稳上"}, "invalid_decision_schema")]
        for changes, reason in cases:
            with self.subTest(changes=changes), patch.object(
                    self.service.llm_service, "generate_llm_json",
                    side_effect=lambda *args, **kwargs: self.response(*args, **kwargs, **changes)):
                selected, metadata = self.service.select_candidates(self.payload, self.jobs, 1)
            self.assertEqual(selected[0]["job_id"], "rule-first")
            self.assertEqual(metadata["fallback_reason"], reason)

    def test_missing_required_schema_and_short_ranking_fall_back(self):
        for response, limit in [({"llm_used": True}, 1),
                                ({"llm_used": True, "ranked_jobs": "not a list"}, 1)]:
            with self.subTest(response=response), patch.object(
                    self.service.llm_service, "generate_llm_json", return_value=response):
                selected, metadata = self.service.select_candidates(self.payload, self.jobs, limit)
            self.assertEqual(selected[0]["job_id"], "rule-first")
            self.assertEqual(metadata["fallback_reason"], "invalid_ranking_schema")
        with patch.object(self.service.llm_service, "generate_llm_json", side_effect=self.response):
            selected, metadata = self.service.select_candidates(self.payload, self.jobs, 2)
        self.assertEqual(len(selected), 2)
        self.assertEqual(metadata["fallback_reason"], "incomplete_ranking")

    def test_pending_candidates_always_remain_explicitly_pending(self):
        for job in self.jobs:
            job["eligibility_status"] = "pending"
            job["ai_analysis"] = {"qualification_match": "全部符合"}
        with patch.object(self.service.llm_service, "generate_llm_json", side_effect=self.response):
            selected, metadata = self.service.select_candidates(self.payload, self.jobs, 1)
        decision = selected[0]["selection_decision"]
        self.assertEqual(selected[0]["eligibility_status"], "pending")
        self.assertEqual(decision["recommendation_grade"], "待核验备选")
        self.assertIn("待核验", decision["rationale"])
        self.assertIn("待核验", selected[0]["ai_analysis"]["qualification_match"])
        self.assertTrue(metadata["model_used"])

    def test_pending_cannot_be_promoted_to_confirmed_eligibility_by_model_text(self):
        self.jobs[1]["eligibility_status"] = "pending"
        with patch.object(self.service.llm_service, "generate_llm_json", side_effect=lambda *args, **kwargs:
                          self.response(*args, **kwargs, rationale="资格全部符合，可以直接报考。")):
            selected, metadata = self.service.select_candidates(self.payload, self.jobs, 1)
        self.assertEqual(selected[0]["job_id"], "rule-first")
        self.assertEqual(metadata["fallback_reason"], "invalid_eligibility_claim")

    def test_pending_cautious_negative_survives_but_a_contradictory_claim_does_not(self):
        self.jobs[1]["eligibility_status"] = "pending"
        for rationale, accepted in [
                ("资格尚待核验，不能确认可以报考，软件开发方向符合偏好。", True),
                ("资料待核验，但整体符合，可以报名。", False)]:
            with self.subTest(rationale=rationale), patch.object(
                    self.service.llm_service, "generate_llm_json", side_effect=lambda *args, **kwargs:
                    self.response(*args, **kwargs, rationale=rationale)):
                selected, metadata = self.service.select_candidates(self.payload, self.jobs, 1)
            self.assertEqual(metadata["model_used"], accepted)
            self.assertEqual(selected[0]["job_id"], "software-second" if accepted else "rule-first")

    def test_missing_pinned_skill_can_use_an_ordinary_model_without_claiming_skill(self):
        with patch.object(self.service.analysis_skill_service, "get_analysis_skill_status",
                          return_value={"ready": False}), patch.object(
                self.service.llm_service, "generate_llm_json", side_effect=self.response):
            selected, metadata = self.service.select_candidates(self.payload, self.jobs, 1)
        self.assertEqual(selected[0]["job_id"], "software-second")
        self.assertTrue(metadata["model_used"])
        self.assertFalse(metadata["skill_applied"])
        self.assertEqual(metadata["skill_revision"], "")

    def test_skill_off_still_allows_preference_ranking_without_claiming_skill_applied(self):
        self.payload.question = "不用这个方法，推荐软件开发方向"
        seen = {}

        def model(prompt, fallback_data, analysis_system_prompt=""):
            seen["system"] = analysis_system_prompt
            return self.response(prompt, fallback_data, analysis_system_prompt)

        with patch.object(self.service.llm_service, "generate_llm_json", side_effect=model):
            selected, metadata = self.service.select_candidates(self.payload, self.jobs, 1)
        self.assertEqual(selected[0]["job_id"], "software-second")
        self.assertTrue(metadata["model_used"])
        self.assertFalse(metadata["skill_applied"])
        self.assertEqual(metadata["skill_revision"], "")
        self.assertNotIn("先硬筛，再优化", seen["system"])

    def test_pool_cap_and_result_limit_are_bounded_and_scope_is_visible(self):
        seen = []

        def model(prompt, fallback_data, analysis_system_prompt=""):
            data = json.loads(prompt)
            seen.append(data)
            return {"llm_used": False}

        with patch.dict("os.environ", {"SELECTION_CANDIDATE_POOL_LIMIT": "1"}), patch.object(
                self.service.llm_service, "generate_llm_json", side_effect=model):
            selected, metadata = self.service.select_candidates(self.payload, self.jobs, 50)
        self.assertEqual(len(selected), 1)
        self.assertEqual(len(seen[0]["candidates"]), 1)
        self.assertEqual(metadata["candidate_count"], 2)
        self.assertEqual(metadata["pool_count"], 1)
        self.assertTrue(metadata["pool_truncated"])
        self.assertEqual(metadata["scope"], "filtered_candidate_pool")

    def test_empty_pool_and_nonpositive_limits_do_not_call_a_model(self):
        with patch.object(self.service.llm_service, "generate_llm_json") as call:
            for candidates, limit in [([], 1), (self.jobs, 0), (self.jobs, -1)]:
                selected, metadata = self.service.select_candidates(self.payload, candidates, limit)
                self.assertEqual(selected, [])
                self.assertFalse(metadata["model_used"])
        call.assert_not_called()

    def test_selection_reserves_time_for_the_final_report(self):
        observed = []

        @contextmanager
        def stage(name, reserve_seconds=0):
            observed.append((name, reserve_seconds))
            yield

        with patch.object(self.service.llm_service, "model_stage", stage, create=True), patch.object(
                self.service.llm_service, "generate_llm_json", side_effect=self.response):
            selected, _ = self.service.select_candidates(self.payload, self.jobs, 1)
        self.assertEqual(selected[0]["job_id"], "software-second")
        self.assertEqual(observed, [("selection", 30)])

    def test_model_exceptions_return_rules_without_echoing_exception_details(self):
        with patch.object(self.service.llm_service, "generate_llm_json",
                          side_effect=RuntimeError("fiction-private-secret")):
            selected, metadata = self.service.select_candidates(self.payload, self.jobs, 1)
        self.assertEqual(selected[0]["job_id"], "rule-first")
        self.assertEqual(metadata["fallback_reason"], "model_call_failed")
        self.assertNotIn("fiction-private-secret", json.dumps(metadata))


if __name__ == "__main__":
    unittest.main()
