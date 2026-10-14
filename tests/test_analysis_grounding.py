"""Visible model claims must agree with the specific job, without format gates."""
import importlib.util
import json
import unittest
from copy import deepcopy
from types import SimpleNamespace


class AnalysisGroundingTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.find_spec("app.services.analysis_grounding_service")
        self.assertIsNotNone(spec, "The grounding service has not been implemented")
        from app.services import analysis_grounding_service
        self.service = analysis_grounding_service
        self.payload = SimpleNamespace(education="本科", major="软件工程", identity="应届生", question="推荐一个岗位", profile={})
        self.job = {"position_code": "A001", "position_name": "信息管理岗", "recruit_count": 2,
                    "min_interview_score": 120, "score_match_type": "exact_position_code",
                    "major_required": "软件工程", "education_required": "本科", "identity_required": "不限",
                    "eligibility_status": "matched", "eligibility_checks": [
                        {"field": "专业", "status": "matched", "reason": "专业名称精确匹配"}],
                    "applicants_count": None, "competition_ratio": None}
        self.fallback = {"why_recommended": "可先作为候选。", "qualification_match": "资格初步匹配，仍需官方审核。",
                         "major_match": "专业名称初步匹配。", "direction_fit": "结合职责和职业偏好比较。",
                         "recruit_risk": "招录2人，仍有波动风险。", "competition_data": "历史最低进面分120，报名和竞争比未知。",
                         "verify_before_apply": "核对公告和资格材料。"}

    def card(self, **updates):
        return self.service.ground_card(self.payload, self.job, {**self.fallback, **updates}, self.fallback)

    def test_false_score_in_any_visible_field_is_repaired(self):
        for field in self.fallback:
            with self.subTest(field=field):
                cleaned, meta = self.card(**{field: "该岗位最低进面分999分。"})
                self.assertNotIn("999", cleaned[field])
                self.assertIn(field, meta["repaired_fields"])
                self.assertTrue(meta["reasons"])

    def test_partially_true_score_does_not_hide_a_false_second_claim(self):
        cleaned, meta = self.card(competition_data="最低进面分120分，另一个最低进面分999分。")
        self.assertNotIn("999", cleaned["competition_data"])
        self.assertIn("120", cleaned["competition_data"])
        self.assertIn("competition_data", meta["repaired_fields"])

    def test_invented_applicants_ratio_recruitment_and_score_are_rejected(self):
        for claim in ("已报名999人", "竞争比1:1", "该岗位招录42人", "42分即可进入面试"):
            with self.subTest(claim=claim):
                cleaned, meta = self.card(why_recommended=claim)
                self.assertNotEqual(cleaned["why_recommended"], claim)
                self.assertIn("why_recommended", meta["repaired_fields"])

    def test_ratio_zero_applicants_and_real_count_are_evidence(self):
        self.job.update(applicants_count=0, competition_ratio="5:1")
        text = "报名人数0人，竞争比5:1，招录2人，历史最低进面分120分。"
        cleaned, meta = self.card(competition_data=text)
        self.assertEqual(cleaned["competition_data"], text)

    def test_promises_are_rejected_even_from_competition_data(self):
        cleaned, meta = self.card(competition_data="最低进面分120，承诺稳上。")
        self.assertNotIn("稳上", cleaned["competition_data"])
        self.assertIn("competition_data", meta["repaired_fields"])

    def test_cautious_negated_claims_and_subjective_tradeoffs_survive(self):
        text = "不能据此断言工资高，也不能保证稳上；若重视技术积累，可优先核对职责。"
        cleaned, meta = self.card(direction_fit=text)
        self.assertEqual(cleaned["direction_fit"], text)
        self.assertTrue(meta["accepted"])
        json.dumps(meta, ensure_ascii=False)

    def test_unmet_or_pending_does_not_become_matched_in_any_field(self):
        for status in ("pending", "unmet"):
            self.job["eligibility_status"] = status
            self.job["eligibility_checks"][0]["status"] = status
            for field in self.fallback:
                with self.subTest(status=status, field=field):
                    cleaned, meta = self.card(**{field: "你完全符合全部资格，可以直接报名，专业完全匹配。"})
                    self.assertNotIn("可以直接报名", cleaned[field])
                    self.assertNotIn("专业名称初步匹配", cleaned[field])
                    self.assertIn(field, meta["repaired_fields"])

    def test_major_claim_needs_a_matched_major_check(self):
        self.job["eligibility_checks"][0]["status"] = "pending"
        cleaned, meta = self.card(major_match="你的软件工程专业完全符合岗位要求。")
        self.assertNotIn("完全符合", cleaned["major_match"])
        self.assertIn("major_match", meta["repaired_fields"])

    def test_nonexact_history_cannot_be_labeled_the_jobs_exact_score(self):
        self.job["score_match_type"] = "similar_reference"
        cleaned, meta = self.card(competition_data="这个岗位的官方最低进面分为120分。")
        self.assertNotEqual(cleaned["competition_data"], "这个岗位的官方最低进面分为120分。")
        self.assertIn("competition_data", meta["repaired_fields"])

    def test_short_report_without_eight_headings_is_kept(self):
        text = "建议把信息管理岗作为技术方向的备选。不能据此断言工资高。历史最低进面分120仅作参考。"
        result, meta = self.service.ground_report(self.payload, [self.job], text, "规则报告")
        self.assertEqual(result, text)
        self.assertEqual(meta["source"], "model")
        self.assertTrue(meta["accepted"])

    def test_report_repairs_false_claim_while_preserving_useful_reasoning(self):
        text = "若重视技术积累，建议先核对信息化职责。该岗位最低进面分999分，报名999人，竞争比1:1。"
        result, meta = self.service.ground_report(self.payload, [self.job], text, "历史最低进面分120分。")
        self.assertIn("若重视技术积累", result)
        self.assertNotIn("999", result)
        self.assertNotIn("1:1", result)
        self.assertEqual(meta["source"], "mixed")
        self.assertTrue(meta["accepted"])

    def test_report_checks_scores_per_job_not_union_of_known_numbers(self):
        second = {**deepcopy(self.job), "position_code": "B002", "position_name": "综合岗", "min_interview_score": 130}
        text = "A001：最低进面分130分。\nB002：最低进面分120分。"
        result, meta = self.service.ground_report(self.payload, [self.job, second], text, "按岗位核验历史分数。")
        self.assertNotIn("A001：最低进面分130", result)
        self.assertNotIn("B002：最低进面分120", result)
        self.assertFalse(meta["accepted"])
        self.assertEqual(meta["source"], "rules")

    def test_report_scoping_survives_heading_and_next_sentence(self):
        second = {**deepcopy(self.job), "position_code": "B002", "position_name": "综合岗", "min_interview_score": 130}
        text = "A001 信息管理岗\n历史最低进面分130分。\nB002 综合岗\n历史最低进面分130分。"
        result, meta = self.service.ground_report(self.payload, [self.job, second], text, "核对分数。")
        self.assertEqual(result.count("历史最低进面分130分。"), 1)
        self.assertEqual(meta["source"], "mixed")

    def test_non_string_card_value_is_not_rendered_as_fact(self):
        cleaned, meta = self.card(competition_data={"score": 999})
        self.assertNotIn("999", cleaned["competition_data"])
        self.assertIn("competition_data", meta["repaired_fields"])

    def test_fallback_itself_does_not_override_pending_eligibility(self):
        self.job["eligibility_status"] = "pending"
        self.fallback["qualification_match"] = "可以直接报名，全部资格已满足。"
        cleaned, meta = self.card()
        self.assertNotIn("可以直接报名", cleaned["qualification_match"])
        self.assertIn("待核验", cleaned["qualification_match"])

    def test_unlabeled_second_score_is_not_hidden_behind_a_true_score(self):
        cleaned, meta = self.card(competition_data="最低进面分120，实际应为999。")
        self.assertNotIn("999", cleaned["competition_data"])
        self.assertIn("competition_data", meta["repaired_fields"])

    def test_reverse_counts_and_labelled_numeric_ratios_are_verified(self):
        for text in ("已有999名考生报名", "竞争比为1", "报录比1∶1", "42个招录名额"):
            with self.subTest(text=text):
                cleaned, meta = self.card(direction_fit=text)
                self.assertNotEqual(cleaned["direction_fit"], text)
                self.assertIn("direction_fit", meta["repaired_fields"])

    def test_negative_qualification_and_pending_questions_are_preserved(self):
        self.job["eligibility_status"] = "unmet"
        self.job["eligibility_checks"][0]["status"] = "unmet"
        for text in ("专业不符合要求，需要选择其他岗位。", "专业是否匹配仍待核验。", "专业匹配情况需要核验。"):
            with self.subTest(text=text):
                cleaned, meta = self.card(major_match=text)
                self.assertEqual(cleaned["major_match"], text)

    def test_false_explicit_requirements_and_salary_amounts_are_repaired(self):
        for text in ("岗位专业要求为法学。", "岗位学历要求为硕士。", "岗位只限应届生。", "该岗位工资每月9999元。"):
            with self.subTest(text=text):
                cleaned, meta = self.card(why_recommended=text)
                self.assertNotEqual(cleaned["why_recommended"], text)
                self.assertIn("why_recommended", meta["repaired_fields"])

    def test_real_numeric_job_codes_years_and_list_numbers_are_not_scores(self):
        self.job["position_code"] = "300110020001"
        text = "1. 岗位300110020001：2025年历史最低进面分120分，招录2人。"
        result, meta = self.service.ground_report(self.payload, [self.job], text, "规则报告")
        self.assertEqual(result, text)
        self.assertEqual(meta["source"], "model")

    def test_sample_caveat_does_not_hide_a_false_official_score_claim(self):
        self.job["score_match_type"] = "candidate_score_sample"
        cleaned, meta = self.card(competition_data="样本参考120分，官方最低进面分120分。")
        self.assertNotEqual(cleaned["competition_data"], "样本参考120分，官方最低进面分120分。")
        self.assertIn("competition_data", meta["repaired_fields"])

    def test_missing_applicant_major_cannot_be_declared_matched(self):
        self.payload.major = ""
        cleaned, meta = self.card(major_match="你的专业完全匹配岗位要求。")
        self.assertNotIn("完全匹配", cleaned["major_match"])
        self.assertIn("major_match", meta["repaired_fields"])

    def test_conditional_advice_is_preserved_without_confirming_eligibility(self):
        self.job["eligibility_status"] = "pending"
        self.job["eligibility_checks"][0]["status"] = "pending"
        text = "核验通过后可以报名，目前仍应补充材料。"
        cleaned, meta = self.card(verify_before_apply=text)
        self.assertEqual(cleaned["verify_before_apply"], text)

    def test_identical_fallback_is_not_labeled_model_adoption(self):
        cleaned, meta = self.card()
        self.assertFalse(meta["accepted"])
        self.assertEqual(meta["source"], "rules")
        result, meta = self.service.ground_report(self.payload, [self.job], "规则报告", "规则报告")
        self.assertEqual(meta["source"], "rules")
        self.assertFalse(meta["accepted"])

    def test_unknown_position_code_cannot_enter_a_grounded_report(self):
        for text in ("建议优先选择职位代码999999999999的公安执法岗。",
                     "A001信息管理岗值得核对，另推荐岗位C003。"):
            with self.subTest(text=text):
                result, meta = self.service.ground_report(self.payload, [self.job], text, "请核对给定岗位。")
                self.assertNotIn("999999999999", result)
                self.assertNotIn("C003", result)
                self.assertIn("unknown_job_reference", meta["reasons"])

    def test_explicit_location_year_exam_and_duty_conflicts_are_repaired(self):
        self.job.update(city="广州", region="广东", year=2026, exam_type="国考",
                        job_description="业务系统软件开发")
        for text in ("A001位于深圳。", "A001工作地点为湖北。",
                     "A001是2025年国考岗位。", "A001属于省考。",
                     "A001主要职责是财务审计。"):
            with self.subTest(text=text):
                result, meta = self.service.ground_report(self.payload, [self.job], text, "请核对岗位事实。")
                self.assertNotEqual(result, text)
                cleaned, card_meta = self.card(why_recommended=text)
                self.assertNotEqual(cleaned["why_recommended"], text)
                self.assertTrue(meta["reasons"])
                self.assertTrue(card_meta["reasons"])

    def test_supported_core_facts_and_historical_score_year_survive(self):
        self.job.update(city="广州", region="广东", year=2026, exam_type="国考",
                        job_description="业务系统软件开发")
        text = "A001位于广州市，是2026年国考岗位，主要职责是软件开发。2025年历史最低进面分120分，仅作参考。"
        result, meta = self.service.ground_report(self.payload, [self.job], text, "规则报告")
        self.assertEqual(result, text)
        self.assertTrue(meta["accepted"])

    def test_field_checks_override_overall_state_for_negative_and_pending_claims(self):
        self.job["eligibility_status"] = "pending"
        self.job["eligibility_checks"] = [
            {"field": "专业", "status": "matched"}, {"field": "身份", "status": "pending"}]
        for text in ("你的专业不符合岗位要求，不能报名。", "目前专业资格尚待核验。"):
            with self.subTest(text=text):
                cleaned, meta = self.card(major_match=text)
                self.assertNotEqual(cleaned["major_match"], text)
                self.assertIn("qualification_major_state_conflict", meta["reasons"])

    def test_known_unmet_cannot_be_downgraded_to_pending_or_denied(self):
        self.job["eligibility_status"] = "unmet"
        self.job["eligibility_checks"][0]["status"] = "unmet"
        for text in ("目前只是专业资格待核验。", "没有明确不符合项。"):
            with self.subTest(text=text):
                result, meta = self.service.ground_report(self.payload, [self.job], text, "专业不符合要求。")
                self.assertNotEqual(result, text)
                self.assertTrue(meta["reasons"])

    def test_absent_field_check_does_not_inherit_overall_matched(self):
        cleaned, meta = self.card(qualification_match="你的学历已经符合岗位要求。")
        self.assertNotIn("学历已经符合", cleaned["qualification_match"])
        self.assertIn("qualification_education_unverified", meta["reasons"])

    def test_true_negative_pending_and_cautious_official_checks_survive(self):
        self.job["eligibility_status"] = "unmet"
        self.job["eligibility_checks"] = [
            {"field": "专业", "status": "unmet"}, {"field": "身份", "status": "pending"}]
        for text in ("专业不符合要求；身份资格待核验。",
                     "专业是否匹配仍待核验，不能确认可以报名。",
                     "不能保证稳上；需要核验官方资料。"):
            with self.subTest(text=text):
                result, meta = self.service.ground_report(self.payload, [self.job], text, "规则报告")
                self.assertEqual(result, text)
                self.assertTrue(meta["accepted"])

    def test_same_sentence_comparison_binds_scores_counts_and_ratios_to_each_job(self):
        self.job.update(applicants_count=10, competition_ratio="5:1")
        second = {**deepcopy(self.job), "position_code": "B002", "position_name": "综合岗",
                  "min_interview_score": 130, "recruit_count": 3,
                  "applicants_count": 30, "competition_ratio": "10:1"}
        for text in ("A001历史最低进面分120分，B002历史最低进面分130分。",
                     "A001招录2人而B002招录3人。",
                     "A001报名10人、竞争比5:1，B002报名30人、竞争比10:1。"):
            with self.subTest(text=text):
                result, meta = self.service.ground_report(self.payload, [self.job, second], text, "规则报告")
                self.assertEqual(result, text)
                self.assertTrue(meta["accepted"])
        false_text = "A001历史最低进面分130分，B002历史最低进面分120分。"
        result, meta = self.service.ground_report(self.payload, [self.job, second], false_text, "规则报告")
        self.assertNotEqual(result, false_text)
        self.assertIn("unsupported_score_min", meta["reasons"])

    def test_collective_claim_must_match_every_referenced_job(self):
        second = {**deepcopy(self.job), "position_code": "B002", "position_name": "综合岗", "recruit_count": 3}
        text = "A001和B002均招录3人。"
        result, meta = self.service.ground_report(self.payload, [self.job, second], text, "规则报告")
        self.assertNotEqual(result, text)
        self.assertIn("unsupported_recruit_count", meta["reasons"])


    def test_true_nonmatch_wording_and_conditional_verification_survive(self):
        self.job["eligibility_status"] = "unmet"
        self.job["eligibility_checks"][0]["status"] = "unmet"
        text = "专业不匹配岗位要求，仍需核对专业目录。"
        cleaned, meta = self.card(major_match=text)
        self.assertEqual(cleaned["major_match"], text)
        self.assertTrue(meta["accepted"])

    def test_matched_field_can_be_confirmed_while_other_checks_remain_pending(self):
        self.job["eligibility_status"] = "pending"
        self.job["eligibility_checks"] = [
            {"field": "专业", "status": "matched"}, {"field": "身份", "status": "pending"}]
        text = "你的专业完全符合岗位要求，身份资格待核验。"
        cleaned, meta = self.card(qualification_match=text)
        self.assertEqual(cleaned["qualification_match"], text)
        self.assertTrue(meta["accepted"])

    def test_exam_type_assertion_after_job_code_is_checked(self):
        self.job.update(exam_type="国考")
        result, meta = self.service.ground_report(self.payload, [self.job], "A001是省考。", "请核对考试类型。")
        self.assertNotEqual(result, "A001是省考。")
        self.assertIn("unsupported_job_exam_type", meta["reasons"])


if __name__ == "__main__":
    unittest.main()
