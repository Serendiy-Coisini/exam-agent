"""Profile changes must recompute eligibility using isolated fictional jobs."""
import asyncio
import unittest

from tests import test_eligibility as existing
from tests import test_regressions as fixtures


CERT_LIST = '至少具有注册会计师、税务师(注册税务师)、国家统一法律职业资格考试(A类证书)、计算机技术与软件专业技术资格(中级及以上)中的一项资质'


class ProfileSwitchTests(unittest.TestCase):
    setUp = existing.EligibilityTests.setUp
    tearDown = existing.EligibilityTests.tearDown
    row = existing.EligibilityTests.row
    task = existing.EligibilityTests.task
    profile = existing.EligibilityTests.profile
    evaluate = existing.EligibilityTests.evaluate

    def test_certificate_any_one_list_recomputes_for_each_profile_in_both_directions(self):
        cases = [({}, 'unmet'), ({'certificates':['注册会计师']}, 'matched'),
                 ({'certificates':['驾驶证C1']}, 'unmet'), ({'law_a_certificate':True}, 'matched'),
                 ({'law_a_certificate':False,'certificates':['法律职业资格B类']}, 'unmet'),
                 ({'certificates':['税务师']}, 'matched'), ({'certificates':['软考中级']}, 'matched'),
                 ({'certificates':['软考初级']}, 'pending'),
                 ({'certificates_reviewed':None}, 'pending'), ({}, 'unmet')]
        for values,wanted in cases:
            with self.subTest(values=values):
                result=self.evaluate(CERT_LIST,self.profile(**values))
                self.assertEqual(result['status'],wanted,result)

    def test_certificate_names_and_explicit_law_status_do_not_authorize_unrelated_options(self):
        clause='至少具有国际注册会计师(ACCA)、税务师中的一项资质'
        for certificates,wanted in [(['注册会计师'],'unmet'),(['ACCA'],'matched'),
                                     (['税务师'],'matched'),(['驾驶证C1'],'unmet')]:
            self.assertEqual(self.evaluate(clause,self.profile(certificates=certificates))['status'],wanted)
        self.assertEqual(self.evaluate('至少具有注册会计师、税务师中的一项资质(具有者优先)')['status'],'pending')
        self.assertEqual(self.evaluate('至少具有注册会计师、税务师中的一项资质',
                                       self.profile(law_a_certificate=True))['status'],'unmet')
        self.assertEqual(self.evaluate('至少具有注册会计师、税务师中的一项资质',
                                       self.profile(certificates=['美国注册会计师(AICPA)']))['status'],'unmet')

    def test_saved_profile_gender_and_major_switches_recompute_full_job_query(self):
        from app.db import import_repository as repo
        repo.confirm_import_task_data(self.task([
            self.row(job_code='SPACE',major_requirement='080901计算机科学与技术 0809计算机类'),
            self.row(job_code='FEMALE',remark='大学英语四级合格或四级考试成绩425分及以上,仅限女性'),
            self.row(job_code='MALE',remark='限男性'),
            self.row(job_code='CERT',remark=CERT_LIST),
        ]))
        cases=[(self.profile(gender='男'), {'SPACE','MALE'}),
               (self.profile(gender='女',certificates=['税务师']), {'SPACE','FEMALE','CERT'}),
               (self.profile(gender='',certificates_reviewed=None), {'SPACE','MALE','FEMALE','CERT'}),
               (self.profile(gender='男',certificates=['驾驶证C1']), {'SPACE','MALE'}),
               (self.profile(gender='女',major='目录外专业'), {'SPACE','FEMALE'}),
               (self.profile(gender='男'), {'SPACE','MALE'})]
        for profile,wanted in cases:
            with self.subTest(profile=profile):
                status,_=asyncio.run(fixtures.request('PUT','/api/v1/user/profile',profile))
                self.assertEqual(status,200)
                status,data=asyncio.run(fixtures.request('POST','/api/v1/jobs/eligibility',{'status':'candidate'}))
                self.assertEqual(status,200)
                self.assertEqual({job['job_code'] for job in data['items']},wanted,data)

    def test_cleared_explicit_profile_fields_do_not_inherit_saved_certificates_or_gender(self):
        from app.db import import_repository as repo
        repo.confirm_import_task_data(self.task([self.row(job_code='FEMALE',remark='限女性'),self.row(job_code='CERT',remark=CERT_LIST)]))
        status,_=asyncio.run(fixtures.request('PUT','/api/v1/user/profile',self.profile(gender='女',certificates=['注册会计师'])))
        self.assertEqual(status,200)
        for values,wanted in [({'gender':'男','certificates':[],'certificates_reviewed':True},set()),
                              ({'gender':'','certificates':[],'certificates_reviewed':None},{'FEMALE','CERT'})]:
            _,data=asyncio.run(fixtures.request('POST','/api/v1/jobs/eligibility',{'profile':values}))
            self.assertEqual({job['job_code'] for job in data['items']},wanted,data)

    def test_major_whitespace_list_matches_all_profiles_by_their_own_catalog_lineage(self):
        from app.tools.major_hierarchy import check_major
        from tests.test_major_hierarchy import CATALOG
        for profile_entry in CATALOG['entries']:
            profile=dict(major=profile_entry['name'],major_code=profile_entry['code'],education='本科')
            for requirement_entry in CATALOG['entries']:
                requirement='8101晨语类 '+requirement_entry['category_code']+requirement_entry['category_name']
                wanted='matched' if profile_entry['category_code'] in {'8101',requirement_entry['category_code']} else 'unmet'
                result=check_major({'major_requirement':requirement},profile,CATALOG)
                self.assertEqual(result['status'],wanted,result)


if __name__=='__main__':
    unittest.main()
