"""Recruitment year is independent of the calendar year of written exams."""
import asyncio
import unittest

from tests import test_regressions as fixtures
from app.db import import_repository as repo
from app.main import app
from app.tools.data_catalog import search_score_catalog
from app.services import import_service
from app.imports.field_mapper import map_fields
from app.imports.file_reader import RawTable


async def score_request(query):
    messages=[]
    async def receive():return {'type':'http.request','body':b'','more_body':False}
    async def send(message):messages.append(message)
    path='/api/v1/agent/score-references'
    await app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','scheme':'http',
               'method':'GET','path':path,'raw_path':path.encode(),'query_string':query.encode(),
               'headers':[],'client':('127.0.0.1',10),'server':('test',80),'root_path':''},receive,send)
    import json
    return next(m['status'] for m in messages if m['type']=='http.response.start'),json.loads(b''.join(m.get('body',b'') for m in messages if m['type']=='http.response.body'))


class ScoreYearTests(unittest.TestCase):
    setUp=fixtures.AuditRegressions.setUp
    tearDown=fixtures.AuditRegressions.tearDown
    row=fixtures.AuditRegressions.row
    task=fixtures.AuditRegressions.task

    def seed_scores(self):
        repo.confirm_import_task_data(self.task([
            self.row(year=2025,job_code='OLD',min_score=110),
            self.row(year=2026,job_code='NEW',min_score=120),
        ],kind='score_line_table'))

    def test_score_api_filters_recruitment_year_before_limit_and_reports_the_selected_year(self):
        self.seed_scores()
        for year,code in [(2026,'NEW'),(2025,'OLD')]:
            status,data=asyncio.run(score_request(f'year={year}&limit=1'))
            self.assertEqual(status,200)
            self.assertEqual((data['year'],data['total'],data['items'][0]['position_code']),(year,1,code))
            self.assertEqual(data['year_basis'],'recruitment_year')

    def test_missing_recruitment_year_returns_empty_instead_of_other_years(self):
        self.seed_scores()
        status,data=asyncio.run(score_request('year=2024'))
        self.assertEqual(status,200)
        self.assertEqual((data['year'],data['total'],data['items']),(2024,0,[]))
        status,data=asyncio.run(score_request('year=0'))
        self.assertEqual(status,422)

    def test_no_year_means_all_years_without_false_2025_response_metadata(self):
        self.seed_scores()
        data=search_score_catalog()
        self.assertEqual(data['year'],None)
        self.assertEqual({r['year'] for r in data['items']},{2025,2026})

    def test_explicit_recruitment_year_beats_written_exam_year_in_file_context(self):
        for name in ['2025年11月笔试_2026年度国考面试人员名单.xlsx','2025笔试_2026国考进面分.csv',
                     '笔试2025_招考年度2026_国考分数线.xlsx']:
            self.assertEqual(import_service._infer_year(name),2026,name)
        for name in ['2025年11月笔试成绩.xlsx','笔试年份2025_成绩表.csv']:
            self.assertIsNone(import_service._infer_year(name),name)
            defaults,_=import_service._infer_import_defaults(file_name=name,raw_tables=[],table_type='score_line_table',province='全国',exam_type='国考',year=2026)
            self.assertEqual(defaults['year'],2026)
        tables=[RawTable('成绩',[['2025年笔试进面分数线']], 'fiction.xlsx')]
        year=import_service._infer_score_line_year('中央机关2026年度国考面试人员名单.xlsx',tables)
        self.assertEqual(year,2026)

    def test_written_exam_year_column_is_not_mapped_to_recruitment_year(self):
        for kind in ['job_table','score_line_table','candidate_score_table']:
            mapped=map_fields([['笔试年份','招考年度','职位代码','最低进面分'],[2025,2026,'001',120]],kind,header_row_index=0)
            self.assertEqual(mapped.field_mapping.get('year'),'招考年度',kind)
            only_exam=map_fields([['笔试年份','职位代码','最低进面分'],[2025,'001',120]],kind,header_row_index=0)
            self.assertNotIn('year',only_exam.field_mapping,kind)


if __name__=='__main__':unittest.main()
