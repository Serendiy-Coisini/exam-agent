"""Full-profile eligibility uses fictional jobs and an isolated database."""
import asyncio
import unittest

from tests import test_regressions as fixtures
from app.core.config import settings


class EligibilityTests(unittest.TestCase):
    setUp = fixtures.AuditRegressions.setUp
    tearDown = fixtures.AuditRegressions.tearDown
    row = fixtures.AuditRegressions.row
    task = fixtures.AuditRegressions.task

    def profile(self, **values):
        return dict(education='本科', degree='学士', major='软件工程', identity='应届生',
                    graduation_year=2027, gender='男', cet4_score=425, cet6_score=None,
                    certificates=[], certificates_reviewed=True, **{}) | values

    def evaluate(self, remark='', profile=None, **values):
        from app.tools.eligibility_rules import evaluate_job
        return evaluate_job(self.row(remark=remark, **values), profile or self.profile())

    def test_full_query_is_not_limited_to_five_and_pages_cover_all_jobs(self):
        from app.db import import_repository as repo
        repo.confirm_import_task_data(self.task([self.row(job_code=f'{i:08d}') for i in range(27)]))
        all_ids=[]
        for page in (1,2,3):
            status,data=asyncio.run(fixtures.request('POST','/api/v1/jobs/eligibility',
                {'profile':self.profile(),'page':page,'page_size':10,'status':'matched'}))
            self.assertEqual(status,200)
            self.assertEqual(data['total'],27)
            all_ids.extend(job['id'] for job in data['items'])
        self.assertEqual(len(set(all_ids)),27)

    def test_authentication_protects_full_query(self):
        settings.AUTH_ENABLED=True
        self.assertEqual(asyncio.run(fixtures.request('POST','/api/v1/jobs/eligibility',{}))[0],401)

    def test_gender_restriction_and_non_restriction_are_distinguished(self):
        self.assertEqual(self.evaluate('限男性报考。')['status'],'matched')
        self.assertEqual(self.evaluate('限女性报考。')['status'],'unmet')
        self.assertEqual(self.evaluate('男女不限。')['status'],'matched')
        self.assertEqual(self.evaluate('适合男性。')['status'],'pending')
        self.assertEqual(self.evaluate('限男性报考。',self.profile(gender=''))['status'],'pending')

    def test_cet_score_boundaries_and_missing_values(self):
        self.assertEqual(self.evaluate('大学英语四级425分及以上。')['status'],'matched')
        self.assertEqual(self.evaluate('大学英语四级425分以上（含）。')['status'],'matched')
        self.assertEqual(self.evaluate('大学英语四级高于425分。')['status'],'unmet')
        self.assertEqual(self.evaluate('大学英语四级426分及以上。')['status'],'unmet')
        self.assertEqual(self.evaluate('CET-6成绩425分及以上。')['status'],'pending')
        self.assertEqual(self.evaluate('大学英语六级425分及以上。',self.profile(cet6_score=424))['status'],'unmet')

    def test_cet_or_and_combination(self):
        self.assertEqual(self.evaluate('大学英语四级425分及以上或大学英语六级425分及以上。')['status'],'matched')
        self.assertEqual(self.evaluate('大学英语四级425分及以上且大学英语六级425分及以上。')['status'],'pending')
        self.assertEqual(self.evaluate('大学英语四级或六级425分及以上。')['status'],'matched')

    def test_certificate_absent_is_different_from_not_filled(self):
        remark='须取得法律职业资格证书（A类）。'
        self.assertEqual(self.evaluate(remark)['status'],'unmet')
        self.assertEqual(self.evaluate(remark,self.profile(certificates_reviewed=None))['status'],'pending')
        self.assertEqual(self.evaluate(remark,self.profile(certificates=['法律职业资格A类']))['status'],'matched')
        self.assertEqual(self.evaluate(remark,self.profile(certificates=['法律职业资格B类']))['status'],'unmet')

    def test_unknown_conditions_never_become_eligible(self):
        for remark in ['须持专业技能证书。','大学英语四级425分及以上，限本地户籍。',
                       '限男性或持有法律职业资格证书。','本科生须英语四级，研究生须英语六级。',
                       '法律职业资格证书优先。']:
            with self.subTest(remark=remark):
                result=self.evaluate(remark)
                self.assertEqual(result['status'],'pending')
                self.assertTrue(any(c['status']=='pending' and c['evidence'] for c in result['checks']))

    def test_known_conflict_is_not_hidden_by_unknown_conditions(self):
        self.assertEqual(self.evaluate('限女性报考；须本地户籍。')['status'],'unmet')

    def test_partial_major_name_is_not_exact_qualification(self):
        self.assertNotEqual(self.evaluate(major_requirement='软件工程技术')['status'],'matched')
        self.assertEqual(self.evaluate(major_requirement='软件工程、网络工程')['status'],'matched')
        self.assertEqual(self.evaluate(profile=self.profile(education=''),education_requirement='本科')['status'],'pending')

    def test_basics_only_leaves_unchecked_remarks_pending(self):
        from app.tools.eligibility_rules import evaluate_job
        result=evaluate_job(self.row(remark='限女性报考'),self.profile(),check_remarks=False)
        self.assertEqual(result['status'],'pending')
        self.assertEqual(result['basic_status'],'matched')


    def test_unknown_or_branch_does_not_wrongly_exclude(self):
        self.assertEqual(self.evaluate('大学英语四级500分及以上或具有海外学习经历。')['status'],'pending')
        self.assertEqual(self.evaluate('限男性或女性。',self.profile(gender='女'))['status'],'matched')
        self.assertEqual(self.evaluate('大学英语四级或六级高于425分。')['status'],'pending')

    def test_city_year_and_remark_refinement_are_applied_before_paging(self):
        from app.db import import_repository as repo
        repo.confirm_import_task_data(self.task([
            self.row(job_code='A',year=2026,region='南宁市',remark='限男性报考'),
            self.row(job_code='B',year=2027,region='南宁市',remark='大学英语四级425分及以上'),
            self.row(job_code='C',year=2027,region='桂林市',remark='限男性报考'),
            self.row(job_code='D',year=2027,region='南宁市',remark='限女性报考')]))
        payload={'profile':self.profile(),'province':'广西','city':'南宁','status':'all'}
        status,data=asyncio.run(fixtures.request('POST','/api/v1/jobs/eligibility',payload))
        self.assertEqual(status,200)
        self.assertEqual({j['job_code'] for j in data['items']},{'B','D'})
        self.assertEqual(data['counts'],{'matched':1,'pending':0,'unmet':1})
        _,data=asyncio.run(fixtures.request('POST','/api/v1/jobs/eligibility',dict(payload,remark_include='四级')))
        self.assertEqual([j['job_code'] for j in data['items']],['B'])
        _,data=asyncio.run(fixtures.request('POST','/api/v1/jobs/eligibility',dict(payload,year=2026)))
        self.assertEqual([j['job_code'] for j in data['items']],['A'])

    def test_profile_fields_round_trip_and_use_stored_profile(self):
        profile=self.profile(cet4_score=0,cet6_score=500,certificates=['法律职业资格A类'])
        status,data=asyncio.run(fixtures.request('PUT','/api/v1/user/profile',profile))
        self.assertEqual(status,200)
        for field in ['gender','cet4_score','cet6_score','certificates','certificates_reviewed','graduation_year']:
            self.assertEqual(data['profile'][field],profile[field])
        from app.db import import_repository as repo
        repo.confirm_import_task_data(self.task([self.row(remark='限女性报考')]))
        _,data=asyncio.run(fixtures.request('POST','/api/v1/jobs/eligibility',{'status':'all'}))
        self.assertEqual(data['items'][0]['status'],'unmet')

    def test_raw_extras_are_checked_but_not_exposed(self):
        from app.db import import_repository as repo
        import json
        row=self.row(extra_fields_json=json.dumps({'性别':'女性','内部文件路径':'private-test-path'},ensure_ascii=False))
        repo.confirm_import_task_data(self.task([row]))
        _,data=asyncio.run(fixtures.request('POST','/api/v1/jobs/eligibility',{'profile':self.profile(),'status':'all'}))
        self.assertEqual(data['items'][0]['status'],'unmet')
        self.assertNotIn('private-test-path',json.dumps(data))
        self.assertNotIn('extra_fields_json',data['items'][0])

    def test_invalid_pagination_and_profile_scores_rejected(self):
        for payload in [{'page_size':101},{'page':0},{'profile':{'cet4_score':711}},{'profile':{'gender':'invalid'}}]:
            self.assertEqual(asyncio.run(fixtures.request('POST','/api/v1/jobs/eligibility',payload))[0],422)


    def test_province_alias_has_same_result(self):
        from app.db import import_repository as repo
        repo.confirm_import_task_data(self.task())
        _,data=asyncio.run(fixtures.request('POST','/api/v1/jobs/eligibility',
            {'profile':self.profile(),'province':'广西壮族自治区'}))
        self.assertEqual(data['total'],1)


    def test_review_complex_education_and_major_parentheses(self):
        for requirement in ['全日制本科及以上','本科及以上（不含专升本）']:
            self.assertEqual(self.evaluate(education_requirement=requirement)['status'],'pending')
        self.assertEqual(self.evaluate(profile=self.profile(gender='女'),major_requirement='软件工程（限男性）')['status'],'unmet')

    def test_review_negated_or_alternative_identity_and_politics(self):
        self.assertNotEqual(self.evaluate(profile=self.profile(identity='往届生'),identity_requirement='非应届毕业生')['status'],'unmet')
        self.assertNotEqual(self.evaluate(profile=self.profile(identity='往届生'),identity_requirement='应届毕业生或社会人员')['status'],'unmet')
        self.assertNotEqual(self.evaluate(profile=self.profile(political_status='中共党员'),political_requirement='非中共党员')['status'],'matched')

    def test_review_english_proof_alternative_and_not_less_than(self):
        self.assertEqual(self.evaluate('大学英语四级成绩425分及以上（或取得四级合格证书）',self.profile(cet4_score=424,cet4_passed=True))['status'],'matched')
        self.assertEqual(self.evaluate('大学英语四级成绩不低于425分',self.profile(cet4_score=424))['status'],'unmet')
        self.assertEqual(self.evaluate('大学英语四级成绩不低于425分')['status'],'matched')

    def test_review_alternative_markers_are_not_mandatory_conditions(self):
        self.assertEqual(self.evaluate('大学英语四级500分及以上、大学英语六级425分及以上，满足其中一项即可',self.profile(cet4_score=425,cet6_score=500))['status'],'matched')
        self.assertEqual(self.evaluate('限男性（女性具有法律职业资格证书也可报考）',self.profile(gender='女',certificates=['法律职业资格']))['status'],'pending')

    def test_review_explicit_city_conflict_excludes_national_job(self):
        from app.services.eligibility_service import _location
        self.assertFalse(_location({'province':'全国','region':'北京市'},'广西','南宁')[0])

    def test_review_extra_field_paths_are_never_returned(self):
        from app.db import import_repository as repo
        import json
        row=self.row(extra_fields_json=json.dumps({'证书文件路径':'private-test-path'},ensure_ascii=False))
        repo.confirm_import_task_data(self.task([row]))
        _,data=asyncio.run(fixtures.request('POST','/api/v1/jobs/eligibility',{'profile':self.profile(),'status':'all'}))
        self.assertNotIn('private-test-path',json.dumps(data))


    def test_major_specific_parenthetical_does_not_restrict_other_major(self):
        self.assertEqual(self.evaluate(profile=self.profile(major='网络工程',gender='女'),
            major_requirement='软件工程（限男性）、网络工程')['status'],'pending')


    def test_pass_proof_prefix_does_not_inherit_other_exam_score(self):
        self.assertEqual(self.evaluate('通过大学英语四级或大学英语六级500分及以上',
            self.profile(cet4_score=450,cet4_passed=True,cet6_score=425))['status'],'matched')
