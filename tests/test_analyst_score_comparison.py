"""Candidate comparisons must receive evidence before making a selection."""
import json
from unittest.mock import patch

import unittest
from tests import test_analyst_repair_integration as integration
from app.services import selection_decision_service
from app.tools import score_tool


class AnalystScoreComparisonTests(unittest.TestCase):
    setUp = integration.AnalystRepairIntegrationTests.setUp
    job = integration.AnalystRepairIntegrationTests.job
    payload = integration.AnalystRepairIntegrationTests.payload
    run_jobs = integration.AnalystRepairIntegrationTests.run_jobs

    def test_model_sees_scores_for_all_candidates_before_choosing(self):
        seen = {}

        def score_lookup(**kwargs):
            seen['looked_up'] = [job['position_code'] for job in kwargs['matched_jobs']]
            return [dict(score_id=code, position_code=code, department_code='FICTION',
                target='公务员', exam_type='国考', region='广东', year=2027,
                min_interview_score=value, min_score=value, source_type='score_line')
                for code, value in [('111111', 60), ('222222', 120)]]

        def model(prompt, fallback_data, **kwargs):
            candidates = json.loads(prompt)['candidates']
            seen['scores'] = {c['position_code']: c.get('min_score') for c in candidates}
            seen['years'] = {c['position_code']: c.get('score_source_year') for c in candidates}
            selected = min(candidates, key=lambda c: c.get('min_score') or 999)
            return {'llm_used': True, 'ranked_jobs': [dict(candidate_id=selected['candidate_id'],
                recommendation_grade='待核验备选', rationale='历史分数较低，仅作为参考。',
                tradeoff='历史分数不能说明今年竞争，资格仍待核验。', evidence_refs=['min_score'])]}

        with patch('app.agents.exam_agent.search_scores', side_effect=score_lookup), \
                patch.object(selection_decision_service.llm_service, 'generate_llm_json', side_effect=model):
            result = self.run_jobs(self.payload('推荐一个揭阳税务岗，最低进面分低一点', result_limit=1),
                [self.job('111111'), self.job('222222')])
        self.assertCountEqual(seen['looked_up'], ['111111', '222222'])
        self.assertEqual(seen['scores'], {'111111': 60, '222222': 120})
        self.assertEqual(seen['years'], {'111111': 2027, '222222': 2027})
        self.assertEqual([j.position_code for j in result.matched_jobs], ['111111'])
        self.assertEqual([s.position_code for s in result.score_references], ['111111'])

    def test_internal_score_lookup_can_cover_more_than_eight_candidates(self):
        rows = [dict(score_id=str(i), position_code=str(100000+i), department_code='FICTION',
            target='公务员', exam_type='国考', region='广东', year=2027,
            min_interview_score=60+i, min_score=60+i, source_type='score_line') for i in range(12)]
        jobs = [self.job(row['position_code']) for row in rows]
        with patch.object(score_tool, '_read_scores', return_value=rows):
            result = score_tool.search_scores('公务员', '国考', '广东', '软件工程',
                matched_jobs=jobs, limit=len(jobs))
            public_default = score_tool.search_scores('公务员', '国考', '广东', '软件工程', matched_jobs=jobs)
        self.assertEqual(len(result), 12)
        self.assertEqual(len(public_default), 8)
