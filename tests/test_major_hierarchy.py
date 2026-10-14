"""Strict major rules use an entirely fictional undergraduate catalog."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


CATALOG = {
    'status': 'ready', 'source_basename': 'fictional.pdf', 'source_sha256': 'a' * 64,
    'year': 2099, 'entries': [
        dict(code='800101', name='星算工程', discipline_code='80', discipline_name='星工学',
             category_code='8001', category_name='星算类', source_page=3, notes=''),
        dict(code='800102T', name='星图工程', discipline_code='80', discipline_name='星工学',
             category_code='8001', category_name='星算类', source_page=3, notes='授予星学学位'),
        dict(code='800201K', name='星造工程', discipline_code='80', discipline_name='星工学',
             category_code='8002', category_name='星造类', source_page=4, notes=''),
        dict(code='810101', name='晨语学', discipline_code='81', discipline_name='晨文学',
             category_code='8101', category_name='晨语类', source_page=5, notes=''),
    ],
}


class MajorHierarchyTests(unittest.TestCase):
    def check(self, requirement, **profile):
        from app.tools.major_hierarchy import check_major
        actual = dict(major='星算工程', education='本科') | profile
        return check_major({'major_requirement': requirement}, actual, CATALOG)

    def test_unrestricted_does_not_need_profile_or_catalog(self):
        from app.tools.major_hierarchy import check_major
        result = check_major({'major_requirement': '专业不限'}, {}, {'status': 'missing', 'entries': []})
        self.assertEqual((result['status'], result['restriction_level']), ('matched', 'unrestricted'))

    def test_all_catalog_majors_resolve_outward_without_broadening_specific_jobs(self):
        for row in CATALOG['entries']:
            profile=dict(major=row['name'],major_code=row['code'])
            for target in CATALOG['entries']:
                result=self.check(target['code'],**profile)
                self.assertEqual(result['status'],'matched' if row['code']==target['code'] else 'unmet')
                self.assertIn('具体专业匹配' if row['code']==target['code'] else '具体专业不符合',result['reason'])
            for key,label in [('category_name','专业类'),('discipline_name','门类')]:
                result=self.check(row[key],**profile)
                self.assertEqual(result['status'],'matched')
                self.assertIn(label+'匹配',result['reason'])
                path=result['reason'].split('；')[0]
                self.assertLess(path.index(row['name']),path.index(row['category_name']))
                self.assertLess(path.index(row['category_name']),path.index(row['discipline_name']))

    def test_nested_hierarchy_accepts_any_allowed_positive_level(self):
        result=self.check('晨文学(晨语类(未编目细分方向))')
        self.assertEqual(result['status'],'unmet')
        self.assertIn('专业类不符合',result['reason'])
        self.assertNotIn('门类匹配',result['reason'])
        result=self.check('星工学(星造类(未编目细分方向))')
        self.assertEqual(result['status'],'matched')
        self.assertIn('门类匹配',result['reason'])
        self.assertEqual(self.check('星工学(星算类(未编目细分方向))')['status'],'matched')

    def test_outer_rejection_is_per_alternative_instead_of_global(self):
        result=self.check('晨文学(晨语类(未编目细分方向))、星算工程')
        self.assertEqual(result['status'],'matched')
        self.assertNotIn('门类不符合',result['reason'])
        self.assertIn('具体专业匹配',result['reason'])

    def test_specific_alternative_is_checked_before_category_and_discipline(self):
        result=self.check('星工学、星算类、星算工程')
        self.assertEqual(result['status'],'matched')
        self.assertIn('具体专业匹配',result['reason'])
        self.assertNotIn('专业类匹配',result['reason'])
        self.assertNotIn('门类匹配',result['reason'])

    def test_nested_specific_restrictions_are_evaluated_before_their_parents(self):
        result=self.check('星工学(星算类(星算工程))')
        self.assertEqual(result['status'],'matched')
        self.assertIn('具体专业匹配',result['reason'])
        self.assertNotIn('专业类匹配',result['reason'])
        self.assertNotIn('门类匹配',result['reason'])

    def test_no_matching_level_is_unmet_even_with_unlisted_job_major_names(self):
        for requirement in ['晨语类、未编目晨语方向', '未编目方向', '星算工程技术', '',
                            '晨语类(未编目细分方向)', '不存在的专业代码999999']:
            self.assertEqual(self.check(requirement)['status'],'unmet',requirement)
        self.assertEqual(self.check('星算类、未编目晨语方向')['status'],'matched')
        self.assertEqual(self.check('星工学、未编目晨语方向')['status'],'matched')
        self.assertEqual(self.check('星算工程、未编目晨语方向')['status'],'matched')
        self.assertEqual(self.check('专业不限、未编目晨语方向')['status'],'matched')

    def test_any_positive_level_matches_but_explicit_only_and_exclusion_still_apply(self):
        self.assertEqual(self.check('星算类(星图工程)')['status'],'matched')
        self.assertEqual(self.check('星算类(未编目方向)')['status'],'matched')
        self.assertEqual(self.check('星算类(仅限星图工程)')['status'],'unmet')
        self.assertEqual(self.check('星算类(仅限星算工程)')['status'],'matched')
        self.assertEqual(self.check('星算类(不含星算工程)')['status'],'unmet')
        self.assertEqual(self.check('星算类(不含星图工程)')['status'],'matched')

    def test_whitespace_separates_complete_catalog_terms_without_merging_them(self):
        for requirement,status in [('8002星造类 8001星算类','matched'),
            ('星造类\t星算类','matched'),('800201K星造工程 800101星算工程','matched'),
            ('星造类 晨语类','unmet'),('8002 星造类 8001 星算类','matched'),
            ('星 算 工 程','matched'),('星造类 未编目方向','unmet')]:
            with self.subTest(requirement=requirement):
                result=self.check(requirement)
                self.assertEqual(result['status'],status,result)
                self.assertEqual(result['evidence'],requirement)

    def test_exact_specialty_and_hierarchy(self):
        for requirement, level in [('星算工程', 'specialty'), ('800101星算工程', 'specialty'),
                                   ('星算类', 'category'), ('8001', 'category'),
                                   ('星工学', 'discipline'), ('80星工学', 'discipline')]:
            with self.subTest(requirement=requirement):
                result = self.check(requirement)
                self.assertEqual((result['status'], result['restriction_level']), ('matched', level))
                self.assertEqual(result['evidence'], requirement)

    def test_unknown_job_terms_do_not_override_other_allowed_levels(self):
        for requirement, wanted in [('星造类', 'unmet'), ('晨文学', 'unmet'),
                                    ('晨语学', 'unmet'), ('星算工程技术', 'unmet'),
                                    ('星造类、未编目专业', 'unmet'), ('星算类或未编目专业', 'matched')]:
            self.assertEqual(self.check(requirement)['status'], wanted)

    def test_explicit_only_parentheses_narrow_the_allowed_category(self):
        result = self.check('星算类（仅限星图工程）')
        self.assertEqual(result['status'], 'unmet')
        self.assertEqual(result['restriction_level'], 'specialty')
        self.assertEqual(self.check('星算类（仅限星图工程、星算工程）')['status'], 'matched')
        self.assertEqual(self.check('星算类（仅限人工智能方向）')['status'], 'unmet')

    def test_known_outer_category_rejects_unrelated_major_despite_unknown_inner_name(self):
        requirement = '大学本科:8101晨语类(晨语学、未编目晨语方向);研究生:8001星算类'
        result = self.check(requirement)
        self.assertEqual((result['status'], result['restriction_level']), ('unmet', 'category'))
        self.assertEqual(result['evidence'], requirement)
        self.assertEqual(self.check('星算类(星算工程、未编目星算方向)')['status'], 'matched')

    def test_unknown_parenthetical_terms_do_not_prevent_positive_matches(self):
        self.assertEqual(self.check('晨语类(未编目方向)、星算工程')['status'], 'matched')
        self.assertEqual(self.check('晨语类(未编目方向)、星造类(未编目方向)')['status'], 'unmet')
        self.assertEqual(self.check('晨语类(未编目方向)、星算类(未编目方向)')['status'], 'matched')
        self.assertEqual(self.check('星算类(不含未编目方向)')['status'], 'matched')
        self.assertEqual(self.check('晨语类(不含未编目方向)')['status'], 'unmet')

    def test_positive_known_levels_match_and_unrecognized_levels_do_not(self):
        for requirement,status in [('晨语类(或星算工程)','matched'), ('晨语类(其他相关专业也可)','unmet'),
            ('未编目类(星造工程)','unmet'), ('晨语类(限男性)、星算工程','matched'), ('晨语学(限男性)、星算工程','matched')]:
            self.assertEqual(self.check(requirement)['status'],status,requirement)
        self.assertEqual(self.check('晨语类(未编目方向)', education='研究生')['status'], 'pending')

    def test_exclusions_match_exact_catalog_terms(self):
        self.assertEqual(self.check('星算类（不含星算工程）')['status'], 'unmet')
        self.assertEqual(self.check('星算类（不含星图工程）')['status'], 'matched')
        self.assertEqual(self.check('星算类（不含未编目专业）')['status'], 'matched')

    def test_profile_conflicts_and_partial_profile_names_never_match(self):
        for values in [dict(major_code='800102T'), dict(major_category='星造类'),
                       dict(major_discipline='晨文学'), dict(major='星算'),
                       dict(major='星算类'), dict(major_code='800102'), dict(major_code='999999')]:
            self.assertEqual(self.check('星工学', **values)['status'], 'pending')

    def test_suffix_and_manual_hierarchy_are_validated_together(self):
        self.assertEqual(self.check('800102T', major='星图工程', major_code='800102T')['status'], 'matched')
        self.assertEqual(self.check('800102', major='星图工程')['status'], 'unmet')
        self.assertEqual(self.check('星算类', major_category='8001', major_discipline='80')['status'], 'matched')
        self.assertEqual(self.check('800102T星算工程')['status'], 'unmet')

    def test_missing_education_does_not_borrow_bachelor_catalog(self):
        for education in ['', '研究生', '硕士研究生', '专科', '高职']:
            self.assertEqual(self.check('星算类', education=education)['status'], 'pending')
        self.assertEqual(self.check('星算类', education='', education_level='本科')['status'], 'matched')
        self.assertEqual(self.check('星算类', education='研究生', major_education='本科')['status'], 'matched')

    def test_mixed_education_scopes_preserve_original_evidence(self):
        requirement = '大学本科:8001星算类（星算工程）;研究生:8002星造类'
        result = self.check(requirement)
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['evidence'], requirement)
        self.assertEqual(self.check('本科:晨语类;研究生:星算类')['status'], 'unmet')
        self.assertEqual(self.check('研究生:星算类')['status'], 'unmet')

    def test_separate_education_fields_are_scoped(self):
        from app.tools.major_hierarchy import check_major
        job = dict(major_requirement='本科:星算类;研究生:晨语类',
                   major_requirement_bachelor='星算类', major_requirement_graduate='晨语类')
        self.assertEqual(check_major(job, dict(major='星算工程', education='本科'), CATALOG)['status'], 'matched')
        self.assertEqual(check_major(job, dict(major='星算工程', education='研究生'), CATALOG)['status'], 'pending')

    def test_mixed_levels_and_empty_requirement(self):
        result = self.check('星图工程、晨文学')
        self.assertEqual((result['status'], result['restriction_level']), ('unmet', 'mixed'))
        self.assertEqual(self.check('')['status'], 'unmet')

    def test_missing_or_ambiguous_catalog_is_safe(self):
        from app.tools.major_hierarchy import check_major
        for catalog in [{'status': 'missing', 'entries': []},
                        CATALOG | {'entries': CATALOG['entries'] + [CATALOG['entries'][0]]}]:
            self.assertEqual(check_major({'major_requirement': '星算类'},
                                        dict(major='星算工程', education='本科'), catalog)['status'], 'pending')

    def test_discipline_can_have_specialties_without_a_category(self):
        from app.tools.major_hierarchy import check_major
        direct = dict(code='820001TK', name='星际交叉学', discipline_code='82',
                      discipline_name='星际学', category_code='', category_name='', source_page=6)
        catalog = CATALOG | {'entries': CATALOG['entries'] + [direct]}
        for requirement, level in [('星际学', 'discipline'), ('星际交叉学', 'specialty')]:
            result = check_major({'major_requirement': requirement},
                                 dict(major='星际交叉学', education='本科'), catalog)
            self.assertEqual((result['status'], result['restriction_level']), ('matched', level))
        result = check_major({'major_requirement': '8200'},
                             dict(major='星际交叉学', education='本科'), catalog)
        self.assertEqual(result['status'], 'unmet')

    def test_seven_digit_codes_and_punctuation_in_canonical_names(self):
        from app.tools.major_hierarchy import check_major
        special = dict(code='8101100TK', name='晨语、星语与文学', discipline_code='81',
                       discipline_name='晨文学', category_code='8101', category_name='晨语类', source_page=6)
        catalog = CATALOG | {'entries': CATALOG['entries'] + [special]}
        for requirement in ['8101100TK', '晨语、星语与文学', '8101100TK晨语、星语与文学']:
            result = check_major({'major_requirement': requirement},
                                 dict(major='晨语、星语与文学', education='本科'), catalog)
            self.assertEqual(result['status'], 'matched')

    def test_punctuated_catalog_names_remain_whole_in_alternative_lists(self):
        from app.tools.major_hierarchy import check_major
        special=dict(code='8101100TK',name='晨语、星语与文学',discipline_code='81',discipline_name='晨文学',
                     category_code='8101',category_name='晨语类',source_page=6)
        catalog=CATALOG|{'entries':CATALOG['entries']+[special]}
        for requirement in ['晨语、星语与文学、未编目方向','8101100TK晨语、星语与文学、未编目方向']:
            result=check_major({'major_requirement':requirement},{'education':'本科','major':special['name']},catalog)
            self.assertEqual(result['status'],'matched',result)

    def test_names_shared_between_levels_do_not_invalidate_other_terms(self):
        from app.tools.major_hierarchy import check_major
        catalog = copy.deepcopy(CATALOG)
        for entry in catalog['entries']:
            if entry['discipline_code'] == '80':
                entry['discipline_name'] = '星算工程'
        profile = dict(major='星图工程', education='本科')
        for requirement in ['8001', '80星算工程', '星算工程门类', '学科门类:星算工程']:
            self.assertEqual(check_major({'major_requirement': requirement}, profile, catalog)['status'], 'matched')
        self.assertEqual(check_major({'major_requirement': '星算工程'}, profile, catalog)['status'], 'matched')

    def test_conflicting_separate_and_original_fields_remain_pending(self):
        from app.tools.major_hierarchy import check_major
        for raw in ['星造类', '本科:星造类;研究生:星算类']:
            result = check_major(dict(major_requirement=raw, major_requirement_bachelor='星算类'),
                                 dict(major='星算工程', education='本科'), CATALOG)
            self.assertEqual(result['status'], 'pending')
            self.assertIn(raw, result['evidence'])
            self.assertIn('星算类', result['evidence'])

    def test_loader_rejects_incomplete_counts_and_impossible_provenance(self):
        from app.tools import major_hierarchy as module
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            for changes in [dict(counts=dict(specialties=5, categories=3, disciplines=2)),
                            dict(source_page_count=2)]:
                path.write_text(json.dumps(CATALOG | changes), encoding='utf-8')
                with patch.object(module, 'CATALOG_PATH', path):
                    self.assertEqual(module.load_catalog()['status'], 'invalid')

    def test_loader_and_public_catalog_validate_private_data(self):
        from app.tools import major_hierarchy as module
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            with patch.object(module, 'CATALOG_PATH', path):
                self.assertEqual(module.load_catalog()['status'], 'missing')
                path.write_text('{broken', encoding='utf-8')
                self.assertEqual(module.load_catalog()['status'], 'invalid')
                path.write_text(json.dumps(CATALOG, ensure_ascii=False), encoding='utf-8')
                self.assertEqual(module.load_catalog()['status'], 'ready')
                public = module.catalog_public()
                self.assertEqual(public['entries'][1]['code'], '800102T')
                self.assertNotIn(str(path), json.dumps(public, ensure_ascii=False))
                invalid = copy.deepcopy(CATALOG)
                invalid['entries'][0]['category_code'] = '8101'
                path.write_text(json.dumps(invalid), encoding='utf-8')
                self.assertEqual(module.load_catalog()['status'], 'invalid')


if __name__ == '__main__':
    unittest.main()
