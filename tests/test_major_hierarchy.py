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

    def test_exact_specialty_and_hierarchy(self):
        for requirement, level in [('星算工程', 'specialty'), ('800101星算工程', 'specialty'),
                                   ('星算类', 'category'), ('8001', 'category'),
                                   ('星工学', 'discipline'), ('80星工学', 'discipline')]:
            with self.subTest(requirement=requirement):
                result = self.check(requirement)
                self.assertEqual((result['status'], result['restriction_level']), ('matched', level))
                self.assertEqual(result['evidence'], requirement)

    def test_known_other_and_unknown_terms_are_distinct(self):
        for requirement, wanted in [('星造类', 'unmet'), ('晨文学', 'unmet'),
                                    ('晨语学', 'unmet'), ('星算工程技术', 'pending'),
                                    ('星造类、未编目专业', 'pending'), ('星算类或未编目专业', 'pending')]:
            self.assertEqual(self.check(requirement)['status'], wanted)

    def test_parenthetical_specialties_narrow_a_category(self):
        result = self.check('星算类（星图工程）')
        self.assertEqual(result['status'], 'unmet')
        self.assertEqual(result['restriction_level'], 'specialty')
        self.assertEqual(self.check('星算类（星图工程、星算工程）')['status'], 'matched')
        self.assertEqual(self.check('星算类（仅限人工智能方向）')['status'], 'pending')

    def test_exclusions_are_applied_and_unknown_exclusions_stay_pending(self):
        self.assertEqual(self.check('星算类（不含星算工程）')['status'], 'unmet')
        self.assertEqual(self.check('星算类（不含星图工程）')['status'], 'matched')
        self.assertEqual(self.check('星算类（不含未编目专业）')['status'], 'pending')

    def test_profile_conflicts_and_partial_profile_names_never_match(self):
        for values in [dict(major_code='800102T'), dict(major_category='星造类'),
                       dict(major_discipline='晨文学'), dict(major='星算'),
                       dict(major='星算类'), dict(major_code='800102'), dict(major_code='999999')]:
            self.assertEqual(self.check('星工学', **values)['status'], 'pending')

    def test_suffix_and_manual_hierarchy_are_validated_together(self):
        self.assertEqual(self.check('800102T', major='星图工程', major_code='800102T')['status'], 'matched')
        self.assertEqual(self.check('800102', major='星图工程')['status'], 'pending')
        self.assertEqual(self.check('星算类', major_category='8001', major_discipline='80')['status'], 'matched')
        self.assertEqual(self.check('800102T星算工程')['status'], 'pending')

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
        self.assertEqual(self.check('研究生:星算类')['status'], 'pending')

    def test_separate_education_fields_are_scoped(self):
        from app.tools.major_hierarchy import check_major
        job = dict(major_requirement='本科:星算类;研究生:晨语类',
                   major_requirement_bachelor='星算类', major_requirement_graduate='晨语类')
        self.assertEqual(check_major(job, dict(major='星算工程', education='本科'), CATALOG)['status'], 'matched')
        self.assertEqual(check_major(job, dict(major='星算工程', education='研究生'), CATALOG)['status'], 'pending')

    def test_mixed_levels_and_empty_requirement(self):
        result = self.check('星图工程、晨文学')
        self.assertEqual((result['status'], result['restriction_level']), ('unmet', 'mixed'))
        self.assertEqual(self.check('')['status'], 'pending')

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
        self.assertEqual(result['status'], 'pending')

    def test_seven_digit_codes_and_punctuation_in_canonical_names(self):
        from app.tools.major_hierarchy import check_major
        special = dict(code='8101100TK', name='晨语、星语与文学', discipline_code='81',
                       discipline_name='晨文学', category_code='8101', category_name='晨语类', source_page=6)
        catalog = CATALOG | {'entries': CATALOG['entries'] + [special]}
        for requirement in ['8101100TK', '晨语、星语与文学', '8101100TK晨语、星语与文学']:
            result = check_major({'major_requirement': requirement},
                                 dict(major='晨语、星语与文学', education='本科'), catalog)
            self.assertEqual(result['status'], 'matched')

    def test_names_shared_between_levels_do_not_invalidate_other_terms(self):
        from app.tools.major_hierarchy import check_major
        catalog = copy.deepcopy(CATALOG)
        for entry in catalog['entries']:
            if entry['discipline_code'] == '80':
                entry['discipline_name'] = '星算工程'
        profile = dict(major='星图工程', education='本科')
        for requirement in ['8001', '80星算工程', '星算工程门类', '学科门类:星算工程']:
            self.assertEqual(check_major({'major_requirement': requirement}, profile, catalog)['status'], 'matched')
        self.assertEqual(check_major({'major_requirement': '星算工程'}, profile, catalog)['status'], 'pending')

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
