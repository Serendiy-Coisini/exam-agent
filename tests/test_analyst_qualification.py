"""The analyst must share the profile eligibility rules, not semantic major guesses."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db import import_repository as repo
from app.tools import job_tool, major_hierarchy
from tests import test_regressions as fixtures


class AnalystQualificationTests(unittest.TestCase):
    row=fixtures.AuditRegressions.row
    task=fixtures.AuditRegressions.task
    tearDown=fixtures.AuditRegressions.tearDown

    def setUp(self):
        fixtures.AuditRegressions.setUp(self)
        catalog=Path(self.temp.name)/'catalog.json'
        catalog.write_text(json.dumps({'status':'ready','source_basename':'fiction.pdf','source_sha256':'a'*64,'year':2026,
            'entries':[dict(code='080902',name='软件工程',discipline_code='08',discipline_name='工学',
                            category_code='0809',category_name='计算机类',source_page=1,notes='')]}),encoding='utf-8')
        p=patch.object(major_hierarchy,'CATALOG_PATH',catalog);p.start();self.patches.append(p)

    def search(self, rows, profile=None, major='软件工程'):
        repo.confirm_import_task_data(self.task(rows))
        return job_tool.search_jobs(target='公务员',exam_type='省考',region='广西',city='',education='本科',major=major,identity='不限',
            profile=profile or {'degree':'学士','gender':'男','certificates':[],'certificates_reviewed':True})

    def test_related_information_or_data_major_does_not_make_software_eligible(self):
        rows=self.search([self.row(job_code='10000001',major_requirement='大数据管理与应用'),
                          self.row(job_code='10000002',major_requirement='软件工程')])
        self.assertEqual([x['position_code'] for x in rows],['10000002'])

    def test_category_and_discipline_candidates_are_not_lost_by_legacy_name_scoring(self):
        rows=self.search([self.row(job_code='10000001',major_requirement='工学'),
                          self.row(job_code='10000002',major_requirement='0809计算机类')])
        self.assertEqual({x['position_code'] for x in rows},{'10000001','10000002'})

    def test_gender_and_certificate_requirements_use_the_current_profile(self):
        source=[self.row(job_code='10000001',major_requirement='软件工程',remark='限女性报考'),
                self.row(job_code='10000002',major_requirement='软件工程',remark='须持注册会计师证书')]
        self.assertEqual(self.search(source),[])
        female=self.search(source,{'degree':'学士','gender':'女','certificates':['注册会计师'],'certificates_reviewed':True})
        self.assertEqual({x['position_code'] for x in female},{'10000001','10000002'})

    def test_requirement_extras_and_unknowns_are_not_silently_dropped(self):
        source=self.row(major_requirement='软件工程')
        source['extra_fields_json']=json.dumps({'性别':'女'})
        self.assertEqual(self.search([source]),[])
        source['extra_fields_json']=json.dumps({'英语要求':'大学英语六级425分以上'})
        pending=self.search([source])
        self.assertEqual(pending[0]['eligibility_status'],'pending')
        self.assertTrue(any(c['field']=='英语' or '英语' in c['evidence'] for c in pending[0]['eligibility_checks']))


    def test_model_cannot_turn_pending_qualification_into_fully_eligible_card(self):
        from app.agents import exam_agent
        from app.schemas.agent_schema import AgentAnalyzeRequest
        payload=AgentAnalyzeRequest(question='分析这个岗位',education='本科',major='软件工程',identity='应届生')
        job={'position_code':'FICTION001','eligibility_status':'pending',
             'eligibility_checks':[{'field':'证书','status':'pending','evidence':'资格证书','reason':'未填写'}],
             'ai_analysis':{'qualification_match':'资格尚待核验，不能确认整体可报'}}
        with patch.object(exam_agent,'generate_llm_json',return_value={
                'qualification_match':'满足全部报名条件','why_recommended':'整体完全符合，满足全部报名条件，可以直接报考','direction_fit':'可以直接报考','llm_used':True}):
            exam_agent._generate_job_card_analyses(payload,[job])
        self.assertNotIn('满足全部',job['ai_analysis']['qualification_match'])
        self.assertIn('待核验',job['ai_analysis']['qualification_match'])
        self.assertNotIn('满足全部',job['ai_analysis']['why_recommended'])
        self.assertNotIn('可以直接报考',job['ai_analysis']['direction_fit'])


    def test_legacy_import_catalog_cannot_broaden_authoritative_major_path(self):
        catalog=dict(year=2026,province='guangxi',major_name='软件工程',major_code='080902',major_category='管理学类',degree_level='本科')
        repo.confirm_import_task_data(self.task([catalog], 'major_catalog', 'import_legacy_catalog'))
        self.assertEqual(self.search([self.row(major_requirement='管理学类')]),[])


    def test_current_identity_does_not_inherit_a_conflicting_old_fresh_scope(self):
        from app.tools.profile_context import current_qualification_profile
        from app.tools.eligibility_rules import evaluate_job
        old={'identity':'应届生','fresh_graduate_scope':'current_year','is_fresh_graduate':True,'graduation_year':2026}
        job={'year':2026,'education_requirement':'本科','major_requirement':'不限专业','identity_requirement':'2026年应届高校毕业生'}
        for identity,expected in [('往届生','unmet'),('普通人员','pending')]:
            profile=current_qualification_profile(old,'本科','软件工程',identity)
            self.assertEqual(evaluate_job(job,profile)['status'],expected)
        previous={**old,'identity':'往届生','fresh_graduate_scope':'not_fresh','is_fresh_graduate':False}
        profile=current_qualification_profile(previous,'本科','软件工程','应届生')
        self.assertEqual(evaluate_job(job,profile)['status'],'matched')


if __name__=='__main__':
    unittest.main()
