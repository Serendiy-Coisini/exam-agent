"""Request semantics are separate from persisted qualification facts."""
import importlib.util
import unittest
from unittest.mock import patch

from app.schemas.agent_schema import AgentAnalyzeRequest
from app.tools import job_category, region_resolver


class SelectionPlanTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("app.tools.selection_plan"),
                             "The shared request selection plan must exist")
        from app.tools.selection_plan import build_selection_plan, apply_selection_plan
        self.build = build_selection_plan
        self.apply = apply_selection_plan
        aliases = tuple(dict(alias=city, normalized_alias=city, city=city, province=province,
                             district="", level="city")
                        for city, province in (("揭阳", "广东"), ("汕头", "广东"), ("南宁", "广西")))
        self.city_patch = patch.object(region_resolver, "_imported_city_aliases", return_value=aliases)
        self.city_patch.start()
        self.addCleanup(self.city_patch.stop)

    def payload(self, question, **changes):
        values = dict(question=question, region="广东", city="汕头", education="本科",
                      major="软件工程", identity="应届生", exam_type="国考", year=2027,
                      profile={"major":"软件工程", "major_code":"080902", "education":"本科",
                               "identity":"应届生", "fresh_graduate_scope":"current_year",
                               "is_fresh_graduate":True})
        return AgentAnalyzeRequest(**(values | changes))

    def test_excluded_major_does_not_replace_applicant_major(self):
        payload = self.payload("不要法学岗，按我的画像推荐")
        plan = self.build(payload)
        self.assertNotIn("major", plan["updates"])
        self.assertEqual(plan["job_constraints"]["excluded_majors"], ["法学"])
        applied = self.apply(payload)
        self.assertEqual(applied.major, "软件工程")
        self.assertEqual(applied.profile, payload.profile)
        self.assertEqual(applied.selection_constraints["excluded_majors"], ["法学"])

    def test_unrestricted_job_requirement_preserves_personal_major(self):
        for question in ("推荐不限专业的公安岗", "只看专业不限岗位"):
            with self.subTest(question=question):
                plan = self.build(self.payload(question))
                self.assertTrue(plan["job_constraints"]["major_unrestricted"])
                self.assertNotIn("major", plan["updates"])

    def test_negated_unrestricted_requirement_is_not_positive(self):
        plan = self.build(self.payload("不要不限专业岗"))
        self.assertFalse(plan["job_constraints"]["major_unrestricted"])
        self.assertNotIn("major", plan["updates"])

    def test_nonfresh_identity_overrides_stale_fresh_facts_without_mutating_profile(self):
        for question in ("我不是应届生", "非应届毕业生，推荐岗位", "我是往届生", "我不是应届毕业生"):
            with self.subTest(question=question):
                payload = self.payload(question)
                applied = self.apply(payload)
                self.assertEqual(applied.identity, "往届生")
                self.assertEqual(applied.profile, payload.profile)
                self.assertEqual(payload.identity, "应届生")

    def test_job_identity_requirement_does_not_change_candidate(self):
        plan = self.build(self.payload("不要应届生岗位"))
        self.assertNotIn("identity", plan["updates"])

    def test_explicit_self_description_can_override_personal_facts(self):
        plan = self.build(self.payload("我是法学专业，学历是硕士，我是往届生，推荐广西岗位"))
        self.assertEqual(plan["updates"]["major"], "法学")
        self.assertEqual(plan["updates"]["education"], "硕士")
        self.assertEqual(plan["updates"]["identity"], "往届生")
        self.assertEqual(plan["updates"]["region"], "广西")
        self.assertEqual(plan["updates"]["city"], "")

    def test_shorthand_profile_remains_supported(self):
        for question in ("广西 本科 软件工程 应届生", "推荐广西软件工程本科应届生能报岗位"):
            plan = self.build(self.payload(question, region="广东", education="硕士", major="法学", identity="不限"))
            self.assertEqual({key:plan["updates"][key] for key in ("region","education","major","identity")},
                             dict(region="广西",education="本科",major="软件工程",identity="应届生"))

    def test_qualification_requirement_and_negation_are_not_applicant_facts(self):
        for question in ("不要硕士岗位，只看法学岗", "公安岗要求法学专业吗", "应届生政策怎么认定"):
            plan = self.build(self.payload(question))
            self.assertFalse(set(plan["updates"]) & {"major", "education", "identity"}, question)
        self.assertEqual(self.build(self.payload("不要硕士岗位"))["job_constraints"]["education_exclusions"], ["硕士"])

    def test_mixed_recommendation_and_score_preference_stays_recommendation(self):
        plan = self.build(self.payload("推荐揭阳公安岗最低进面分低一点"))
        self.assertEqual(plan["intent"], "recommendation")
        self.assertEqual(plan["updates"]["city"], "揭阳")
        self.assertEqual(plan["category_preference"], {"include":["公安"], "exclude":[]})

    def test_current_explicit_code_takes_priority_without_using_stale_context(self):
        payload = self.payload("分析岗位代码123456789012", current_job={"position_code":"999999999999"},
                               messages=[{"role":"assistant","content":"推荐岗位代码888888888888"}])
        plan = self.build(payload)
        self.assertEqual(plan["position_code"], "123456789012")
        self.assertEqual(plan["intent"], "detail")
        self.assertEqual(self.build(self.payload("推荐揭阳公安岗", current_job=payload.current_job))["position_code"], "")

    def test_short_position_codes_next_to_chinese_are_explicit_details(self):
        for question in ("分析222222这个岗位能不能报", "分析 222222 这个岗位能不能报"):
            plan = self.build(self.payload(question))
            self.assertEqual(plan["position_code"], "222222", question)
            self.assertEqual(plan["intent"], "detail")
        self.assertEqual(self.build(self.payload("推荐2026年省考岗位"))["position_code"], "")

    def test_negated_code_and_background_code_do_not_steal_current_code(self):
        plan = self.build(self.payload("不要岗位代码111111111111，分析岗位代码222222222222"))
        self.assertEqual(plan["position_code"], "222222222222")
        plan = self.build(self.payload("之前看过岗位代码111111111111，现在推荐公安岗"))
        self.assertEqual(plan["position_code"], "")
        self.assertEqual(plan["intent"], "recommendation")

    def test_exam_type_and_year_explicitly_override_scope(self):
        for question in ("推荐2026年广西省考岗位", "不是国考，是2026年广西省考"):
            plan = self.build(self.payload(question))
            self.assertEqual(plan["updates"]["exam_type"], "省考")
            self.assertEqual(plan["updates"]["year"], 2026)
            self.assertEqual(plan["updates"]["region"], "广西")

    def test_negated_and_historical_scopes_are_not_positive(self):
        plan = self.build(self.payload("之前看广西省考，现在不要汕头，只推荐揭阳国考岗位，不看2026年"))
        self.assertEqual(plan["updates"].get("region"), "广东")
        self.assertEqual(plan["updates"].get("city"), "揭阳")
        self.assertEqual(plan["updates"].get("exam_type"), "国考")
        self.assertNotIn("year", plan["updates"])
        plan = self.build(self.payload("不要广西省考岗位"))
        self.assertNotIn("region", plan["updates"])
        self.assertNotIn("exam_type", plan["updates"])

    def test_negated_scopes_propagate_to_adjacent_fields(self):
        plan = self.build(self.payload("不要2026年广西省考，只看2027年广东国考"))
        self.assertEqual(plan["updates"].get("year"), 2027)
        self.assertEqual(plan["updates"].get("region"), "广东")
        self.assertEqual(plan["updates"].get("exam_type"), "国考")

    def test_score_and_policy_requests_have_distinct_intents(self):
        self.assertEqual(self.build(self.payload("揭阳公安岗历年进面分多少"))["intent"], "score")
        self.assertEqual(self.build(self.payload("应届生报考政策怎么认定"))["intent"], "policy")
        self.assertIsNone(self.build(self.payload("你好"))["intent"])

    def test_nonfresh_job_policy_mention_does_not_change_candidate(self):
        for question in ("往届生政策怎么认定", "不要往届生岗位", "非应届毕业生政策有什么变化"):
            self.assertNotIn("identity", self.build(self.payload(question))["updates"], question)

    def test_unknown_major_explicit_self_statement_is_supported(self):
        plan = self.build(self.payload("我的专业是审计学，我的学历是本科，推荐岗位"))
        self.assertEqual(plan["updates"].get("major"), "审计学")
        self.assertEqual(plan["updates"].get("education"), "本科")

    def test_denied_candidate_major_is_not_a_job_exclusion(self):
        plan = self.build(self.payload("我不是法学专业，我是软件工程专业"))
        self.assertEqual(plan["job_constraints"]["excluded_majors"], [])
        self.assertEqual(plan["updates"].get("major"), "软件工程")
        plan = self.build(self.payload("我不是软件工程专业"))
        self.assertEqual(plan["updates"].get("major"), "")
        self.assertTrue(plan["warnings"])

    def test_explicit_job_requirement_is_not_explicit_self_major(self):
        for question in ("这个岗位专业是法学", "岗位要求专业是审计学"):
            self.assertNotIn("major", self.build(self.payload(question))["updates"], question)

    def test_unknown_self_major_with_suffix_is_supported(self):
        plan = self.build(self.payload("我是审计学专业，推荐岗位"))
        self.assertEqual(plan["updates"].get("major"), "审计学")

    def test_education_ceiling_is_a_job_constraint(self):
        plan = self.build(self.payload("只看本科及以下的岗位"))
        self.assertEqual(plan["job_constraints"].get("education_max"), "本科")
        self.assertNotIn("education", plan["updates"])

    def test_past_exam_experience_is_not_current_scope(self):
        plan = self.build(self.payload("我2026年参加过广西国考，推荐广东省考岗位"))
        self.assertNotIn("year", plan["updates"])
        self.assertEqual(plan["updates"].get("region"), "广东")
        self.assertEqual(plan["updates"].get("exam_type"), "省考")

    def test_graduation_year_is_not_exam_year(self):
        for question in ("我是2026年毕业的本科软件工程应届生", "2026届软件工程本科毕业生"):
            self.assertNotIn("year", self.build(self.payload(question))["updates"], question)

    def test_current_specific_city_can_narrow_a_previous_broad_scope(self):
        plan = self.build(self.payload("全国都看，只看揭阳岗位"))
        self.assertEqual(plan["updates"].get("region"), "广东")
        self.assertEqual(plan["updates"].get("city"), "揭阳")
        plan = self.build(self.payload("揭阳岗位，全省都看"))
        self.assertEqual(plan["updates"].get("city"), "不限")

    def test_education_ceiling_with_major_words_does_not_override_applicant(self):
        plan = self.build(self.payload("只看本科及以下软件工程岗位", education="硕士"))
        self.assertNotIn("education", plan["updates"])

    def test_background_hukou_and_residence_are_not_destination_scope(self):
        for question in ("户籍是广西南宁，按画像推荐", "我住在广西南宁，按画像推荐"):
            plan = self.build(self.payload(question))
            self.assertFalse(set(plan["updates"]) & {"region", "city"}, question)

    def test_job_fact_shorthand_does_not_override_personal_profile(self):
        for question in ("按我的画像推荐法学本科岗位", "只看法学本科岗位"):
            plan = self.build(self.payload(question))
            self.assertFalse(set(plan["updates"]) & {"major", "education"}, question)

    def test_bare_single_qualification_can_answer_a_followup(self):
        for question, field, value in (("本科", "education", "本科"), ("应届生", "identity", "应届生"),
                                       ("软件工程", "major", "软件工程")):
            plan = self.build(self.payload(question))
            self.assertEqual(plan["updates"].get(field), value, question)

    def test_contextual_continuation_inherits_role_constraints_only(self):
        old_plan = {"category_preference":{"include":["公安"],"exclude":[]},
                    "job_constraints":{"major_unrestricted":True,"excluded_majors":["法学"]},
                    "updates":{"major":"法学","education":"博士"},
                    "position_code":"999999"}
        plan = self.build(self.payload("再推荐几个", selection_plan=old_plan))
        self.assertEqual(plan["category_preference"], {"include":["公安"],"exclude":[]})
        self.assertEqual(plan["job_constraints"].get("category_preference"), plan["category_preference"])
        self.assertTrue(plan["job_constraints"]["major_unrestricted"])
        self.assertEqual(plan["job_constraints"]["excluded_majors"], ["法学"])
        self.assertTrue(plan["inherited"])
        self.assertEqual(plan["position_code"], "")
        self.assertFalse(set(plan["updates"]) & {"major","education"})

    def test_explicit_new_category_replaces_inherited_category(self):
        old_plan = {"category_preference":{"include":["公安"],"exclude":[]},
                    "job_constraints":{"category_preference":{"include":["公安"],"exclude":[]}}}
        plan = self.build(self.payload("再推荐几个税务岗", selection_plan=old_plan))
        self.assertEqual(plan["category_preference"], {"include":["税务"],"exclude":[]})
        self.assertEqual(plan["job_constraints"].get("category_preference"), plan["category_preference"])

    def test_fresh_query_does_not_inherit_old_constraints(self):
        old_plan = {"category_preference":{"include":["公安"],"exclude":[]},
                    "job_constraints":{"major_unrestricted":True,"excluded_majors":["法学"]}}
        plan = self.build(self.payload("推荐广西本科软件工程应届生能报岗位", selection_plan=old_plan))
        self.assertEqual(plan["category_preference"], {"include":[],"exclude":[]})
        self.assertFalse(plan["job_constraints"]["major_unrestricted"])
        self.assertEqual(plan["job_constraints"]["excluded_majors"], [])
        self.assertFalse(plan.get("inherited", False))

    def test_malformed_prior_plan_cannot_inject_facts_or_crash_continuation(self):
        old_plan = {"category_preference":{"include":[{}, "公安"],"exclude":[]},
                    "job_constraints":{"education_max":[], "major":"法学"}}
        plan = self.build(self.payload("还有其他的吗", selection_plan=old_plan))
        self.assertEqual(plan["intent"], "recommendation")
        self.assertEqual(plan["category_preference"]["include"], ["公安"])
        self.assertNotIn("education_max", plan["job_constraints"])
        self.assertNotIn("major", plan["updates"])

    def test_apply_is_request_only_and_retains_question_and_evidence(self):
        payload = self.payload("推荐不限专业的公安岗")
        result = self.apply(payload)
        self.assertIsNot(result, payload)
        self.assertEqual(result.question, payload.question)
        self.assertEqual(result.profile, payload.profile)
        self.assertTrue(result.selection_plan["evidence"])
        self.assertEqual(result.selection_plan["warnings"], [])


class CategoryScopeTests(unittest.TestCase):
    def test_postposed_negation_excludes_category(self):
        self.assertEqual(job_category.extract_job_category_preference("公安岗不要"),
                         {"include":[],"exclude":["公安"]})

    def test_except_negation_includes_only_named_category(self):
        for question in ("不要公安以外的岗位", "公安以外都不要", "除了公安其他都不要"):
            self.assertEqual(job_category.extract_job_category_preference(question),
                             {"include":["公安"],"exclude":[]}, question)

    def test_joint_postposed_negation_excludes_all_named_categories(self):
        self.assertEqual(job_category.extract_job_category_preference("税务海关都不要"),
                         {"include":[],"exclude":["税务","海关"]})

    def test_geographic_gongan_is_not_police_preference_or_agency(self):
        self.assertEqual(job_category.extract_job_category_preference("推荐公安县税务岗，不是公安"),
                         {"include":["税务"],"exclude":["公安"]})
        job = {"department":"国家税务总局公安县税务局", "position_name":"一级行政执法员"}
        self.assertEqual(job_category.job_categories(job), {"税务"})
        self.assertFalse(job_category.matches_job_category(job,{"include":["公安"],"exclude":[]}))

    def test_negative_titles_and_auxiliary_words_do_not_prove_police_agency(self):
        for job in ({"department":"公安县人民政府", "position_name":"综合管理"},
                    {"department":"公安县税务局", "position_name":"非公安岗"},
                    {"department":"综合办公室", "position_name":"公安类专业研究"}):
            self.assertNotIn("公安", job_category.job_categories(job), job)

    def test_explicit_positive_category_after_joint_negation_is_included(self):
        self.assertEqual(job_category.extract_job_category_preference("税务海关都不要只看公安岗"),
                         {"include":["公安"],"exclude":["税务","海关"]})

    def test_long_location_does_not_break_prefix_category_negation(self):
        self.assertEqual(job_category.extract_job_category_preference("不考虑广东省揭阳市公安岗"),
                         {"include":[],"exclude":["公安"]})

    def test_negated_category_titles_are_not_positive_category_evidence(self):
        for title in ("非税务岗", "不是海关岗位", "非边检岗", "非缉私岗", "非铁路公安岗"):
            self.assertEqual(job_category.job_categories({"department":"综合办公室", "position_name":title}), set(), title)

    def test_personal_dont_only_look_statement_broadens_category(self):
        self.assertEqual(job_category.extract_job_category_preference("我不要只看公安岗"),
                         {"include":[], "exclude":[]})

    def test_police_subcategories_remain_real_agencies(self):
        for department, title, categories in (("广州铁路公安局","基层所队民警",{"公安","铁路公安"}),
                ("汕头海关缉私局","一级警长",{"公安","海关","缉私"}),
                ("揭阳出入境边防检查站","执勤民警",{"公安","边检"})):
            self.assertEqual(job_category.job_categories({"department":department,"position_name":title}), categories)


if __name__ == "__main__":
    unittest.main()
