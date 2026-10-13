"""Fictional fixtures for official interview-roster cutoff imports."""
import csv
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app.imports.file_reader import RawTable

from tests import test_regressions as fixtures
from app.db.database import connection
from app.imports import import_store
from app.imports.field_mapper import map_fields
from app.imports.table_detector import detect_table_type, recommend_dataset_outputs
from app.services import import_service


HEADERS = ['准考证号', '姓名', '招录机关', '部门代码', '用人司局', '招考职位', '职位代码', '最低面试分数']
ROWS = [HEADERS,
        ['fiction-1', '虚构甲', '虚构机关', '001', '虚构司局', '虚构职位', '000010', 120.25],
        ['fiction-2', '虚构乙', '虚构机关', '001', '虚构司局', '虚构职位', '000010', 120.25],
        ['fiction-3', '虚构丙', '虚构机关', '002', '另一司局', '另一职位', '000010', 108.5]]


class InterviewCutoffs(unittest.TestCase):
    setUp = fixtures.AuditRegressions.setUp
    tearDown = fixtures.AuditRegressions.tearDown

    def preview(self, rows=ROWS, filename='中央机关2026年度面试人员名单.csv'):
        path = Path(self.temp.name) / filename
        with path.open('w', encoding='utf-8-sig', newline='') as out:
            csv.writer(out).writerows(rows)
        return import_service.build_import_task(path, province='全国', exam_type='国考', year=2026)

    def test_national_roster_infers_scope_without_manual_import_parameters(self):
        self.preview()
        path = Path(self.temp.name) / '中央机关2026年度面试人员名单.csv'
        task = import_service.build_import_task(path)
        self.assertEqual((task['province'], task['exam_type'], task['year']), ('全国', '国考', 2026))
        self.assertEqual(task['status'], 'PREVIEW_READY', task['blocking_reasons'])
        self.assertEqual(task['data_granularity'], 'job_level')

    def test_missing_position_identifier_is_not_aggregated_into_another_position(self):
        rows = [r[:] for r in ROWS]
        rows[1][6] = ''
        rows[2][6] = ''
        task = self.preview(rows)
        self.assertEqual((task['valid_rows'], task['warning_rows']), (1, 2))

    def test_official_minimum_is_a_cutoff_not_a_personal_interview_score(self):
        self.assertEqual(detect_table_type(ROWS).detected_type, 'score_line_table')
        mapped = map_fields(ROWS, 'score_line_table')
        self.assertEqual(mapped.field_mapping.get('min_score'), '最低面试分数')
        self.assertEqual(mapped.field_mapping.get('job_name'), '招考职位')
        self.assertNotIn('interview_score', map_fields(ROWS, 'candidate_score_table').field_mapping)
        self.assertEqual([o['dataset_type'] for o in recommend_dataset_outputs(ROWS)], ['score_line_table'])

    def test_roster_aggregates_cutoffs_and_does_not_persist_candidate_identity(self):
        task = self.preview()
        self.assertEqual(task['detected_type'], 'score_line_table')
        self.assertEqual(task['status'], 'PREVIEW_READY', task.get('blocking_reasons'))
        self.assertEqual((task['total_rows'], task['valid_rows'], task['warning_rows']), (3, 2, 0))
        self.assertEqual(task['import_report']['duplicate_rows'], 0)
        self.assertEqual(task['import_report']['aggregated_roster_rows'], 3)
        rows = task['valid_cleaned_rows']
        self.assertEqual([r['interview_count'] for r in rows], [2, 1])
        self.assertEqual([r['min_score'] for r in rows], [120.25, 108.5])
        self.assertEqual([r['department_code'] for r in rows], ['001', '002'])
        self.assertEqual([r['province'] for r in rows], ['全国', '全国'])
        serialized = json.dumps(rows, ensure_ascii=False)
        for private_value in ('fiction-1', 'fiction-2', '虚构甲', '虚构乙', '准考证号', '姓名'):
            self.assertNotIn(private_value, serialized)

    def test_conflicting_cutoffs_exclude_the_whole_position(self):
        rows = [r[:] for r in ROWS]
        rows[2][-1] = 121
        task = self.preview(rows)
        self.assertEqual(task['valid_rows'], 1)
        self.assertEqual(task['warning_rows'], 2)
        self.assertEqual(task['valid_cleaned_rows'][0]['department_code'], '002')
        self.assertTrue(any(w['field'] == 'cutoff_conflict' for w in task['warnings']))

    def test_invalid_cutoff_excludes_group_instead_of_using_another_candidate(self):
        rows = [r[:] for r in ROWS]
        rows[2][-1] = '错误值'
        task = self.preview(rows)
        self.assertEqual(task['valid_rows'], 1)
        self.assertEqual(task['warning_rows'], 2)

    def test_repeated_candidate_is_counted_once_within_the_position(self):
        task = self.preview(ROWS + [ROWS[1][:]])
        self.assertEqual((task['total_rows'], task['valid_rows'], task['warning_rows']), (4, 2, 0))
        self.assertEqual(task['valid_cleaned_rows'][0]['interview_count'], 2)

    def test_annotated_official_headers_keep_aggregation_and_privacy(self):
        for label in ('最低面试分数（分）', '最低面试分数（含专业考试）'):
            with self.subTest(header=label):
                rows = [r[:] for r in ROWS]
                rows[0][-1] = label
                task = self.preview(rows)
                self.assertEqual(task['detected_type'], 'score_line_table')
                self.assertEqual((task['valid_rows'], task['warning_rows']), (2, 0))
                path = Path(self.temp.name) / task['file_name']
                forced = import_service.build_import_task(path, forced_detected_type='score_line_table')
                self.assertEqual((forced['valid_rows'], forced['warning_rows']), (2, 0))
                self.assertEqual(forced['import_report']['aggregated_roster_rows'], 3)
                for row in forced['valid_cleaned_rows']:
                    self.assertEqual(row['extra_fields_json'], '{}')

    def test_two_row_headers_preserve_cutoff_aggregation_and_privacy(self):
        rows = [r[:] for r in ROWS]
        rows[0][-1] = '进面情况'
        rows.insert(1, ['', '', '', '', '', '', '', '最低面试分数'])
        tables = [RawTable('名单', rows, 'fiction.xlsx')]
        with patch.object(import_service, 'read_import_file', return_value=tables):
            task = import_service.build_import_task(Path('中央机关2026面试名单.xlsx'),
                forced_detected_type='score_line_table', header_row=1, sub_header_row=2,
                data_start_row=3, structure_confirmed=True)
        self.assertEqual((task['valid_rows'], task['warning_rows']), (2, 0))
        self.assertEqual(task['import_report']['aggregated_roster_rows'], 3)
        self.assertEqual(task['valid_cleaned_rows'][0]['interview_count'], 2)
        self.assertTrue(all(row['extra_fields_json'] == '{}' for row in task['valid_cleaned_rows']))

    def test_personal_scores_are_not_inferred_as_cutoffs(self):
        rows = [r[:] for r in ROWS]
        rows[0][-1] = '面试成绩'
        task = self.preview(rows, filename='2026考试成绩.csv')
        self.assertEqual(task['detected_type'], 'candidate_score_table')
        self.assertNotIn('min_score', task['field_mapping'])
        self.assertEqual(task['valid_rows'], 3)

    def test_roster_merges_across_sheets_and_deduplicates_candidate_count(self):
        tables = [RawTable('甲表', [HEADERS, ROWS[1]], 'fiction.xlsx'),
                  RawTable('乙表', [HEADERS, ROWS[1], ROWS[2]], 'fiction.xlsx')]
        with patch.object(import_service, 'read_import_file', return_value=tables):
            task = import_service.build_import_task(Path('中央机关2026面试名单.xlsx'))
        self.assertEqual((task['total_rows'], task['valid_rows'], task['warning_rows']), (3, 1, 0))
        self.assertEqual(task['valid_cleaned_rows'][0]['interview_count'], 2)

    def test_roster_rejects_a_conflicting_position_across_sheets(self):
        conflicting = ROWS[2][:]
        conflicting[-1] = 121
        tables = [RawTable('甲表', [HEADERS, ROWS[1], ROWS[3]], 'fiction.xlsx'),
                  RawTable('乙表', [HEADERS, conflicting], 'fiction.xlsx')]
        with patch.object(import_service, 'read_import_file', return_value=tables):
            task = import_service.build_import_task(Path('中央机关2026面试名单.xlsx'))
        self.assertEqual((task['total_rows'], task['valid_rows'], task['warning_rows']), (3, 1, 2))
        self.assertEqual(task['valid_cleaned_rows'][0]['department_code'], '002')

    def test_confirm_is_idempotent_and_leaves_jobs_empty(self):
        task = self.preview()
        import_store.save_task(task)
        first = import_service.confirm_import_task(task['task_id'])
        second = import_service.confirm_import_task(task['task_id'])
        self.assertEqual(first['status'], 'IMPORTED', first)
        self.assertEqual(first['inserted_count'], 2)
        self.assertEqual(second['inserted_count'], 2)
        with connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM exam_score_lines').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM exam_jobs').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
