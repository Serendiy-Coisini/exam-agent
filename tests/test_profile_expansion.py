"""Fictional extended profile round trips and complete filter integration."""
import asyncio
import unittest
from unittest.mock import patch

from tests.test_major_hierarchy import CATALOG

from tests import test_regressions as fixtures


class ProfileExpansion(unittest.TestCase):
    setUp = fixtures.AuditRegressions.setUp
    tearDown = fixtures.AuditRegressions.tearDown
    row = fixtures.AuditRegressions.row
    task = fixtures.AuditRegressions.task

    def test_new_profile_fields_round_trip_preserving_false_zero_and_lists(self):
        profile = dict(hukou_province='广东', hukou_city='深圳', hukou_county='南山区',
                       grassroots_years=0, fresh_graduate_scope='current_year', graduation_year=2027,
                       service_projects=['三支一扶', '西部计划'], service_projects_reviewed=True,
                       service_completed=False, service_assessment_passed=None, service_years=0,
                       retired_soldier=False, retired_college_graduate=False, military_years=0,
                       law_a_certificate=False, major='虚构专业', major_code='990101',
                       major_discipline='虚构门类', major_category='虚构专业类', major_education='本科')
        status, data = asyncio.run(fixtures.request('PUT', '/api/v1/user/profile', profile))
        self.assertEqual(status, 200)
        for key, value in profile.items():
            with self.subTest(field=key):
                self.assertEqual(data['profile'].get(key), value)
        _, saved = asyncio.run(fixtures.request('GET', '/api/v1/user/profile'))
        for key, value in profile.items():
            self.assertEqual(saved['profile'].get(key), value, key)

    def test_invalid_qualifications_and_restriction_level_are_rejected(self):
        for profile in [dict(military_years=-1), dict(service_years=61),
                        dict(service_projects=['未支持项目']), dict(fresh_graduate_scope='猜测'),
                        dict(hukou_province='x'*81), dict(raw_profile_json={'service_projects':1}),
                        dict(raw_profile_json={'law_a_certificate':[]})]:
            with self.subTest(profile=profile):
                self.assertEqual(asyncio.run(fixtures.request('PUT', '/api/v1/user/profile', profile))[0], 422)
        self.assertEqual(asyncio.run(fixtures.request('POST', '/api/v1/jobs/eligibility',
            {'major_restriction_level': 'invalid'}))[0], 422)


    def test_stored_profile_drives_all_new_qualification_checks(self):
        from app.db import import_repository as repo
        row=fixtures.AuditRegressions.row(self, year=2027, province='guangdong', region='广东省深圳市',
            major_requirement='星算类', grassroots_requirement='二年', identity_requirement='2027年应届毕业生',
            remark='限广东省户籍；三支一扶服务期满且考核合格；须取得法律职业资格证书（A类）')
        repo.confirm_import_task_data(fixtures.AuditRegressions.task(self,[row]))
        profile=dict(education='本科',degree='学士',major='星算工程',graduation_year=2027,fresh_graduate_scope='current_year',
            grassroots_years=2,hukou_province='广东',service_projects=['三支一扶'],service_completed=True,
            service_assessment_passed=True,law_a_certificate=True)
        asyncio.run(fixtures.request('PUT','/api/v1/user/profile',profile))
        with patch('app.tools.major_hierarchy._catalog_state', return_value=(CATALOG,__import__('app.tools.major_hierarchy',fromlist=['_prepare'])._prepare(CATALOG))):
            _,data=asyncio.run(fixtures.request('POST','/api/v1/jobs/eligibility',{'status':'all'}))
            self.assertEqual(data['items'][0]['status'],'matched',data['items'][0]['checks'])
            self.assertEqual(data['items'][0]['major_restriction_level'],'category')
            _,filtered=asyncio.run(fixtures.request('POST','/api/v1/jobs/eligibility',{'major_restriction_level':'specialty'}))
            self.assertEqual(filtered['total'],0)
            _,overridden=asyncio.run(fixtures.request('POST','/api/v1/jobs/eligibility',{'status':'all','profile':{'law_a_certificate':False}}))
            self.assertEqual(overridden['items'][0]['status'],'unmet')

    def test_unknown_catalog_and_unrecognized_hierarchy_never_match(self):
        from app.db import import_repository as repo
        from app.tools import major_hierarchy
        repo.confirm_import_task_data(fixtures.AuditRegressions.task(self,[fixtures.AuditRegressions.row(self,major_requirement='星算工程')]))
        with patch.object(major_hierarchy,'_catalog_state',return_value=({'status':'missing'},None)):
            _,data=asyncio.run(fixtures.request('POST','/api/v1/jobs/eligibility',{'status':'all','profile':{'education':'本科','major':'星算工程'}}))
            self.assertEqual(data['items'][0]['status'],'pending')


if __name__ == '__main__':
    unittest.main()
