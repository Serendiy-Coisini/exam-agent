"""Acceptance tests for the audited analyst decision pipeline, no external calls."""
from contextlib import ExitStack
import unittest
from unittest.mock import patch

from app.agents import exam_agent
from app.schemas.agent_schema import AgentAnalyzeRequest
from app.services.model_settings_service import ModelConfiguration
from app.tools import job_tool, region_resolver


class AnalystRepairIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        patches=[patch.object(exam_agent,'find_job_by_identity',return_value={'status':'not_found','job':None}),
            patch.object(exam_agent,'generate_llm_json',side_effect=lambda **kw:{**kw['fallback_data'],'llm_used':False}),
            patch.object(exam_agent,'generate_llm_summary',side_effect=lambda **kw:{'analysis_report':kw['fallback_report'],'llm_used':False}),
            patch.object(exam_agent,'generate_llm_chat',side_effect=lambda **kw:{'analysis_report':kw['fallback_report'],'llm_used':False}),
            patch.object(exam_agent,'search_job_policy_sources',return_value={'status':'skipped','sources':[]}),
            patch.object(exam_agent,'get_review_score_summary_by_job',return_value={}),
            patch.object(exam_agent,'get_candidate_score_summary_by_job',return_value={}),
            patch.object(exam_agent,'_missing_jobs_region_message',return_value=''),
            patch.object(exam_agent,'search_scores',return_value=[]),
            patch.object(job_tool,'list_imported_major_catalog',return_value=[]),
            patch.object(region_resolver,'_imported_city_aliases',return_value=[]),
            patch('app.services.llm_service.get_model_configuration',return_value=ModelConfiguration('https://fiction.invalid','fiction',''))]
        for p in patches:self.stack.enter_context(p)

    def job(self,code,**changes):
        row=dict(job_id=code,position_code=code,department_code='FICTION',target='公务员',exam_type='国考',
            region='广东',province='广东',city='揭阳',year=2027,department='揭阳市税务局',unit='揭阳市税务局',
            position='一级行政执法员',position_name='一级行政执法员',education_required='本科',
            major_required='软件工程',identity_required='不限',headcount=2)
        row.update(changes)
        return row

    def payload(self,question,**changes):
        fields=dict(question=question,region='广东',city='揭阳',exam_type='国考',year=2027,
            education='本科',major='软件工程',identity='应届生',profile={'major':'软件工程','education':'本科',
            'identity':'应届生','graduation_year':2027,'fresh_graduate_scope':'current_year'})
        fields.update(changes)
        return AgentAnalyzeRequest(**fields)

    def run_jobs(self,payload,rows):
        with patch.object(job_tool,'_read_jobs',return_value=rows):
            return exam_agent._analyze_exam_request_impl(payload)

    def test_negative_major_is_not_applicant_fact(self):
        payload=self.payload('不要法学岗，推荐揭阳税务岗')
        result=self.run_jobs(payload,[self.job('111111'),self.job('222222',major_required='法学')])
        self.assertEqual([j.position_code for j in result.matched_jobs],['111111'])
        self.assertEqual(payload.major,'软件工程')

    def test_unrestricted_major_describes_job_requirement(self):
        result=self.run_jobs(self.payload('推荐不限专业的公安岗'),[
            self.job('111111',department='揭阳市公安局',major_required='不限'),
            self.job('222222',department='揭阳市公安局',major_required='法学'),
            self.job('333333',department='揭阳市公安局')])
        self.assertEqual([j.position_code for j in result.matched_jobs],['111111'])

    def test_not_fresh_clears_old_fresh_assumption(self):
        for question in ('我不是应届生，推荐税务岗','我是往届生，推荐税务岗'):
            result=self.run_jobs(self.payload(question),[
                self.job('111111',identity_required='应届生'),self.job('222222')])
            self.assertEqual([j.position_code for j in result.matched_jobs],['222222'],question)

    def test_explicit_exam_and_year_override_old_filters(self):
        result=self.run_jobs(self.payload('只看2026年国考税务岗，推荐岗位',exam_type='省考'),[
            self.job('111111',year=2026),self.job('222222',year=2026,exam_type='省考')])
        self.assertEqual([(j.position_code,j.exam_type,j.year) for j in result.matched_jobs],[('111111','国考',2026)])

    def test_compound_recommendation_keeps_city_and_category(self):
        result=self.run_jobs(self.payload('推荐揭阳公安岗，最低进面分低一点的'),[
            self.job('111111'),self.job('222222',department='汕头市公安局',city='汕头')])
        self.assertEqual(result.intent,'job_recommendation')
        self.assertEqual(result.matched_jobs,[])
        self.assertEqual(result.score_references,[])

    def test_new_explicit_code_overrides_stale_card(self):
        new=self.job('222222')
        with patch.object(exam_agent,'find_single_job',return_value={'status':'found','job':new,'match_type':'code_exact'}), \
                patch.object(exam_agent,'find_single_score_reference',return_value={'status':'not_found','score':None}):
            result=exam_agent._analyze_exam_request_impl(self.payload('分析222222这个岗位能不能报',
                current_job={'position_code':'111111','job_title':'旧岗位','department':'旧单位'}))
        self.assertEqual([j.position_code for j in result.matched_jobs],['222222'])
        self.assertFalse(result.is_context_followup)

    def test_followup_shows_known_unmet_conditions_and_model_answer(self):
        with patch.object(exam_agent,'generate_llm_chat',return_value={
            'analysis_report':'模型依据当前岗位分析完成。','llm_used':True,'model':'fiction'}):
            result=exam_agent._analyze_exam_request_impl(self.payload('这个岗位我能不能报',current_job={
                'position_code':'111111','job_title':'专业岗位','department':'揭阳测试单位',
                'province':'广东','city':'揭阳','education_requirement':'博士','major_requirement':'法学',
                'identity_requirement':'不限','year':2027,'exam_type':'国考'}))
        self.assertEqual(result.matched_jobs[0].eligibility_status,'unmet')
        self.assertIn('模型依据当前岗位分析完成',result.analysis_report)
        self.assertIn('不符合',result.analysis_report)
        self.assertTrue(result.llm_used)

    def test_model_report_adoption_not_just_call_success(self):
        with patch.object(exam_agent,'generate_llm_summary',return_value={
                'analysis_report':'报名人数为999，竞争比为1:1，稳上。','llm_used':True,'model':'fiction'}):
            result=self.run_jobs(self.payload('推荐揭阳税务岗位'),[self.job('111111')])
        self.assertNotIn('999',result.analysis_report)
        self.assertFalse(result.llm_used)
        self.assertEqual(result.analysis_metadata['report']['source'],'rules')
        self.assertTrue(result.analysis_metadata['report']['reasons'])


    def test_hukou_background_does_not_override_destination_after_planning(self):
        result = self.run_jobs(self.payload('户籍是广西南宁，按画像推荐'), [
            self.job('111111'), self.job('222222', region='广西', province='广西', city='南宁')])
        self.assertEqual([j.position_code for j in result.matched_jobs], ['111111'])
        self.assertEqual(result.resolved_region, '广东')
        self.assertEqual(result.analysis_metadata['effective_conditions']['city'], '揭阳')

    def test_followup_reloads_complete_server_qualification_evidence(self):
        job = self.job('111111', eligibility_extra_requirements='性别：女性')
        payload = self.payload('这个岗位我能不能报', profile={'gender':'男'}, current_job={
            'position_code':'111111', 'department_code':'FICTION', 'job_title':'测试岗位',
            'department':'揭阳测试单位', 'province':'广东', 'city':'揭阳',
            'education_requirement':'本科', 'major_requirement':'软件工程',
            'year':2027, 'exam_type':'国考'})
        with patch.object(exam_agent, 'find_job_by_identity', return_value={
                'status':'found', 'job':job}, create=True) as reload_job:
            result = exam_agent._analyze_exam_request_impl(payload)
        self.assertTrue(reload_job.called)
        self.assertEqual(result.matched_jobs[0].eligibility_status, 'unmet')
        self.assertIn('性别', result.analysis_report)

    def test_source_labels_keep_national_exam_distinct_from_provincial_exam(self):
        for exam, expected in [('国考', '国考'), ('省考', '省考')]:
            payload=self.payload('查看岗位',exam_type=exam)
            result=exam_agent._prepare_job_response_fields(payload,[self.job('111111',exam_type=exam)])[0]
            self.assertIn(expected, result['data_source_label'])
            self.assertIn('2027', result['data_source_label'])

if __name__=='__main__':unittest.main()
