"""Fictional qualification fixtures; unknown information must stay unknown."""
import importlib.util
import unittest


class QualificationRulesTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.find_spec('app.tools.profile_qualification_rules')
        self.assertIsNotNone(spec, 'Extended qualification rules are not implemented')
        from app.tools import profile_qualification_rules
        return profile_qualification_rules

    def check_clause(self, clause, profile, expected, job=None, remaining=''):
        checks, residue = self.module().consume_qualification_clause(clause, profile, job or {})
        self.assertTrue(checks, clause)
        statuses = [item['status'] for item in checks]
        status = 'unmet' if 'unmet' in statuses else ('pending' if 'pending' in statuses else 'matched')
        self.assertEqual(status, expected, (clause, checks))
        self.assertEqual(residue, remaining)
        for item in checks:
            self.assertEqual(set(item), {'field', 'status', 'evidence', 'reason'})
            self.assertTrue(item['evidence'])

    def test_household_uses_actual_registration_and_exact_location(self):
        for clause, profile, job, expected in [
            ('限本省户籍', {'hukou_province':'甲省'}, {'province':'甲省'}, 'matched'),
            ('限本省户籍', {'province':'甲省','target_region':'甲省'}, {'province':'甲省'}, 'pending'),
            ('限本地户籍', {'hukou_province':'乙省'}, {'region':'甲省'}, 'pending'),
            ('限本地户籍', {'hukou_province':'甲省'}, {}, 'pending'),
            ('限青云市户籍', {'hukou_city':'青云市'}, {}, 'matched'),
            ('限青云市户籍', {'hukou_city':'新青云市'}, {}, 'unmet'),
            ('限本县户口', {'hukou_county':'青云县'}, {'county':'青云县'}, 'matched'),
            ('非本地户籍', {'hukou_province':'乙省'}, {'region':'甲省'}, 'pending'),
            ('非本地户籍', {'hukou_province':'甲省'}, {'region':'甲省'}, 'pending'),
            ('户籍不限', {}, {}, 'matched'),
            ('不限户籍', {}, {}, 'matched'),
            ('本省生源或本省户籍', {'hukou_province':'甲省'}, {'province':'甲省'}, 'pending'),
        ]:
            with self.subTest(clause=clause, profile=profile):
                self.check_clause(clause, profile, expected, job)

    def test_combined_household_does_not_capture_other_predicates(self):
        self.check_clause('具有两年基层工作经历和广东省户籍', {'grassroots_years':2,'hukou_province':'广东省'}, 'matched')
        self.check_clause('须取得法律职业资格A类和广东省户籍', {'law_a_certificate':True,'hukou_province':'广东省'}, 'matched')
        self.check_clause('有广东省户籍', {'hukou_province':'广东省'}, 'matched')
        self.check_clause('具有特殊资格和广东省户籍', {'hukou_province':'广东省'}, 'matched', remaining='特殊资格')

    def test_full_project_names_and_generic_soldier_alternative(self):
        for text, project in [('农村义务教育阶段学校教师特设岗位计划','特岗教师'), ('大学生志愿服务西部计划','西部计划'), ('三支一扶计划','三支一扶')]:
            self.check_clause('限'+text+'人员', {'service_projects':[project]}, 'matched')
        profile=dict(service_projects=['高校毕业生退役士兵'],service_projects_reviewed=True,retired_soldier=True,retired_college_graduate=True,military_years=5)
        self.check_clause('服务基层项目人员', profile, 'matched')
        self.check_clause('服务基层项目人员', profile|{'military_years':4}, 'unmet')
        self.check_clause('服务基层项目人员', profile|{'retired_college_graduate':None}, 'pending')
        self.check_clause('服务基层项目人员', {'service_projects':1}, 'pending')

    def test_grassroots_boundaries_and_zero(self):
        for required, actual, expected in [
            ('两年以上基层工作经历',2,'matched'), ('基层工作经历不低于二年',2,'matched'),
            ('基层工作经历大于2年',2,'unmet'), ('基层工作经历大于2年',2.1,'matched'),
            ('基层工作经历2年及以上',0,'unmet'), ('基层工作经历2年及以上',None,'pending'),
            ('基层工作经历2年以下',2,'matched'), ('基层工作经历2年以下',3,'unmet'),
            ('基层工作经历2至3年',2.5,'matched'), ('基层工作经历2至3年',4,'unmet'),
            ('基层工作经历不得少于两年',2,'matched'),
        ]:
            with self.subTest(required=required, actual=actual):
                self.check_clause(required, {'grassroots_years':actual}, expected)

    def test_program_list_is_union_and_reviewed_absence_is_unmet(self):
        self.check_clause('限三支一扶或西部计划人员', {'service_projects':['西部计划']}, 'matched')
        self.check_clause('限三支一扶、特岗教师人员', {'service_projects':['特岗教师']}, 'matched')
        self.check_clause('限三支一扶人员', {'service_projects':['大学生村官'],'service_projects_reviewed':True}, 'unmet')
        self.check_clause('限三支一扶人员', {'service_projects':[]}, 'pending')
        self.check_clause('服务基层项目人员', {'service_projects':['三支一扶']}, 'matched')
        self.check_clause('服务基层项目人员', {'service_projects':[],'service_projects_reviewed':True}, 'unmet')

    def test_service_completion_assessment_and_duration_are_distinct(self):
        profile = dict(service_projects=['三支一扶'],service_completed=True,service_assessment_passed=True,service_years=2)
        self.check_clause('限三支一扶服务期满且考核合格人员', profile, 'matched')
        self.check_clause('限三支一扶服务期满且考核合格人员', profile | {'service_completed':None}, 'pending')
        self.check_clause('限三支一扶服务期满且考核合格人员', profile | {'service_assessment_passed':False}, 'unmet')
        self.check_clause('服务满2年', {'grassroots_years':10}, 'pending')
        self.check_clause('服务满2年', {'service_years':2}, 'matched')

    def test_retired_college_soldier_requires_distinct_evidence(self):
        profile = dict(retired_soldier=True,retired_college_graduate=True,military_years=5)
        text = '限高校毕业生退役士兵，服役5年（含）以上'
        self.check_clause(text, profile, 'matched')
        self.check_clause(text, profile | {'military_years':4.9}, 'unmet')
        self.check_clause(text, profile | {'retired_soldier':False}, 'unmet')
        self.check_clause(text, profile | {'retired_college_graduate':None}, 'pending')
        self.check_clause(text, {'service_projects':['高校毕业生退役士兵'],'grassroots_years':8,'service_years':8}, 'pending')

    def test_fresh_graduate_year_and_retained_scope(self):
        for required, profile, job, expected in [
            ('2029年应届高校毕业生', {'fresh_graduate_scope':'current_year','graduation_year':2029}, {'year':2029}, 'matched'),
            ('2029届毕业生', {'fresh_graduate_scope':'current_year','graduation_year':2028}, {'year':2029}, 'unmet'),
            ('当年应届毕业生', {'fresh_graduate_scope':'current_year','graduation_year':2029}, {}, 'pending'),
            ('当年应届毕业生', {'fresh_graduate_scope':'current_year','graduation_year':2029}, {'year':2029}, 'matched'),
            ('应届毕业生', {'fresh_graduate_scope':'retained','graduation_year':2027}, {'year':2029}, 'matched'),
            ('2029年应届毕业生', {'fresh_graduate_scope':'retained','graduation_year':2029}, {'year':2029}, 'unmet'),
            ('应届毕业生', {'fresh_graduate_scope':'current_year'}, {'year':2029}, 'pending'),
            ('应届毕业生', {'fresh_graduate_scope':'not_fresh'}, {'year':2029}, 'unmet'),
            ('应届毕业生', {'identity':'应届生','graduation_year':2029}, {'year':2029}, 'matched'),
        ]:
            with self.subTest(required=required, profile=profile):
                self.check_clause(required, profile, expected, job)

    def test_class_a_uses_tristate_and_never_accepts_b_or_c(self):
        for profile, expected in [({'law_a_certificate':True},'matched'), ({'law_a_certificate':False},'unmet'),
                                  ({'certificates':['法律职业资格A类']},'matched'),
                                  ({'certificates':['法律职业资格B类'],'certificates_reviewed':True},'unmet')]:
            self.check_clause('须取得法律职业资格证书（A类）', profile, expected)
        self.check_clause('无需法律职业资格A类', {'law_a_certificate':False}, 'matched')
        checks, remaining = self.module().consume_qualification_clause('须取得法律职业资格证书（A类）', {}, {})
        self.assertEqual(checks, [])
        self.assertEqual(remaining, '须取得法律职业资格证书（A类）')

    def test_cross_condition_or_and_unsupported_negation_stay_pending(self):
        for text in ['限本省户籍或大学英语六级425分以上', '非三支一扶人员', '应届毕业生优先',
                     '三支一扶或法律职业资格A类', '除本省户籍外', '限本省户籍或者具有特殊资格']:
            with self.subTest(text=text):
                self.check_clause(text, {'hukou_province':'甲省','law_a_certificate':True}, 'pending', {'province':'甲省'})

    def test_mixed_conditions_preserve_english_and_unknown_residue(self):
        self.check_clause('限本省户籍且大学英语四级425分及以上', {'hukou_province':'甲省'}, 'matched',
                          {'province':'甲省'}, remaining='大学英语四级425分及以上')
        self.check_clause('具有两年以上基层工作经历且身体健康', {'grassroots_years':2}, 'matched', remaining='身体健康')
        self.assertEqual(self.module().consume_qualification_clause('须持教师资格证', {}, {}), ([], '须持教师资格证'))

    def test_hierarchy_names_and_county_types_cannot_cross_match(self):
        self.check_clause('限甲省青云市户籍', {'hukou_province':'甲省','hukou_city':'青云市'}, 'matched')
        self.check_clause('限甲省青云市户籍', {'hukou_province':'乙省','hukou_city':'青云市'}, 'unmet')
        self.check_clause('限青云县户籍', {'hukou_county':'青云区'}, 'unmet')
        self.check_clause('限青云县户籍', {'hukou_county':'青云县'}, 'matched')

    def test_explicit_project_and_is_not_union(self):
        self.check_clause('三支一扶且西部计划人员', {'service_projects':['三支一扶'],'service_projects_reviewed':True}, 'unmet')
        self.check_clause('三支一扶且西部计划人员', {'service_projects':['三支一扶','西部计划']}, 'matched')

    def test_conditional_qualifications_do_not_reject_inapplicable_branches(self):
        for text in ['若为本省户籍须英语六级', '本科生须有基层工作经历2年以上', '应届毕业生可免基层工作经历2年']:
            self.check_clause(text, {'grassroots_years':0,'fresh_graduate_scope':'current_year','graduation_year':2029}, 'pending', {'year':2029})

    def test_extra_common_military_and_class_a_spelling(self):
        self.check_clause('高校毕业生退役士兵，在军队服役5年（含）以上', dict(retired_soldier=True,retired_college_graduate=True,military_years=5), 'matched')
        self.check_clause('须取得A类法律职业资格证书', {'law_a_certificate':True}, 'matched')
        self.check_clause('未取得法律职业资格A类', {'law_a_certificate':False}, 'matched')
        self.check_clause('未取得法律职业资格A类', {'law_a_certificate':True}, 'unmet')

    def test_structured_labels_support_unrestricted_and_known_values(self):
        for label in ['服务基层项目工作经历','服务基层项目','户籍要求','户口要求','基层工作最低年限','应届要求','法律职业资格']:
            self.check_clause(label+'：无限制', {}, 'matched')
        self.check_clause('基层工作最低年限：2年', {'grassroots_years':2}, 'matched')
        self.check_clause('户籍要求：本省', {'hukou_province':'甲省'}, 'matched', {'province':'甲省'})
        self.check_clause('服务基层项目工作经历：三支一扶', {'service_projects':['三支一扶']}, 'matched')
        self.check_clause('应届要求：当年应届毕业生', {'fresh_graduate_scope':'current_year','graduation_year':2029}, 'matched', {'year':2029})
        self.check_clause('法律职业资格：A类', {'law_a_certificate':True}, 'matched')

    def test_explicit_law_false_conflicting_with_a_certificate_is_pending(self):
        self.check_clause('法律职业资格A类', {'law_a_certificate':False,'certificates':['法律职业资格A类']}, 'pending')

    def test_dedicated_identity_field_supports_projects_and_retired_soldiers(self):
        rules = self.module()
        self.assertEqual(rules.evaluate_basic_identity('服务基层项目人员', {'service_projects':['西部计划']}, {})['status'], 'matched')
        self.assertEqual(rules.evaluate_basic_identity('退役大学生士兵', {'retired_soldier':True,'retired_college_graduate':False}, {})['status'], 'unmet')

    def test_project_union_does_not_turn_into_and_due_to_shared_assessment(self):
        self.check_clause('三支一扶、西部计划人员且考核合格', {'service_projects':['西部计划'],'service_projects_reviewed':True,'service_assessment_passed':True}, 'matched')
        self.check_clause('西部计划且三支一扶人员', {'service_projects':['西部计划'],'service_projects_reviewed':True}, 'unmet')

    def test_household_span_does_not_swallow_preceding_unknown_conditions(self):
        self.check_clause('具有特殊资格且本省户籍', {'hukou_province':'甲省'}, 'matched', {'province':'甲省'}, remaining='特殊资格')
        self.check_clause('限本省户籍且青云市户籍', {'hukou_province':'甲省','hukou_city':'青云市'}, 'matched', {'province':'甲省'})

    def test_dedicated_year_field_does_not_guess_unparsed_qualifiers(self):
        rules = self.module()
        for required in ['2年以上但不含2年', '2年左右', '2年且特殊工作性质']:
            self.assertEqual(rules.evaluate_grassroots_requirement(required, {'grassroots_years':2})['status'], 'pending')
        self.assertEqual(rules.evaluate_grassroots_requirement('恰好2年', {'grassroots_years':3})['status'], 'unmet')
        self.assertEqual(rules.evaluate_grassroots_requirement('恰好2年', {'grassroots_years':2})['status'], 'matched')

    def test_negative_municipality_registration_is_inverted(self):
        self.check_clause('非本省户籍', {'hukou_province':'北京市'}, 'unmet', {'province':'北京市'})
        self.check_clause('非本省户籍', {'hukou_province':'甲省'}, 'matched', {'province':'北京市'})

    def test_dedicated_identity_project_alternative_remains_union(self):
        self.assertEqual(self.module().evaluate_basic_identity('三支一扶或西部计划人员', {'service_projects':['西部计划']}, {})['status'], 'matched')

    def test_basic_entrypoints_retain_unknown_semantics(self):
        rules = self.module()
        self.assertEqual(rules.evaluate_basic_identity('应届毕业生', {'fresh_graduate_scope':'retained'}, {'year':2029})['status'], 'matched')
        self.assertEqual(rules.evaluate_grassroots_requirement('2年', {'grassroots_years':None})['status'], 'pending')
        self.assertEqual(rules.evaluate_grassroots_requirement('2年', {'grassroots_years':0})['status'], 'unmet')


class ImportedHouseholdScope(unittest.TestCase):
    def consume(self, clause, profile, job=None):
        from app.tools.profile_qualification_rules import consume_qualification_clause
        return consume_qualification_clause(clause, profile, job or {'province':'guangdong','region':'广东省深圳市南山区'})

    def test_province_slugs_and_negative_local_province_are_canonicalized(self):
        profile={'hukou_province':'广东省'}
        checks,_=self.consume('限本省户籍',profile)
        self.assertEqual(checks[0]['status'],'matched')
        checks,_=self.consume('限非本省户籍',profile)
        self.assertEqual(checks[0]['status'],'unmet')

    def test_local_scope_uses_explicit_city_or_county_instead_of_target_preferences(self):
        profile={'province':'广东','city':'深圳','hukou_province':'广东','hukou_city':'广州','hukou_county':'天河区'}
        checks,_=self.consume('限本市户籍',profile)
        self.assertEqual(checks[0]['status'],'unmet')
        checks,_=self.consume('限本地户籍',profile)
        self.assertEqual(checks[0]['status'],'unmet')
        profile.update(hukou_city='深圳',hukou_county='南山区')
        checks,_=self.consume('限本地户籍',profile)
        self.assertEqual(checks[0]['status'],'matched')

    def test_national_placeholder_is_not_a_parent_or_an_explicit_local_name(self):
        profile={'hukou_province':'广东省','hukou_city':'深圳市'}
        job={'province':'全国','region':'广东省深圳市'}
        self.assertEqual(self.consume('本市户籍',profile,job)[0][0]['status'],'matched')
        self.assertEqual(self.consume('非本市户籍',profile,job)[0][0]['status'],'unmet')
        self.assertEqual(self.consume('本溪市户籍',{'hukou_province':'辽宁省','hukou_city':'本溪市'},job)[0][0]['status'],'matched')

    def test_local_county_checks_known_parents_to_avoid_duplicate_names(self):
        job={'province':'beijing','region':'北京市朝阳区'}
        profile={'hukou_province':'吉林省','hukou_city':'长春市','hukou_county':'朝阳区'}
        for text in ['本区户籍','本地户籍']:
            checks,_=self.consume(text,profile,job)
            self.assertEqual(checks[0]['status'],'unmet')
            checks,_=self.consume(text,{'hukou_county':'朝阳区'},job)
            self.assertEqual(checks[0]['status'],'pending')

    def test_unclear_or_multiple_work_locations_do_not_define_local_hukou(self):
        for region in ['全国','广东省','广东省深圳市或广州市','广东省深圳市、广州市','广东省深圳市及广州市','广东省深圳市南山区和福田区']:
            checks,_=self.consume('限本地户籍',{'hukou_province':'广东','hukou_city':'深圳'}, {'province':'guangdong','region':region})
            self.assertEqual(checks[0]['status'],'pending',region)


if __name__ == '__main__':
    unittest.main()
