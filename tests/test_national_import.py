"""Fictional national-exam fixtures: reused position codes are intentional."""
import csv
import io
from pathlib import Path
from unittest.mock import patch

import unittest
import tests.test_regressions as fixtures
from app.imports.file_reader import RawTable
from app.imports.field_mapper import map_fields
from app.services import import_service
from app.imports import import_store
from app.db import import_repository as repo
from app.tools import job_tool


HEADERS = ["部门代码", "部门名称", "用人司局", "招考职位", "职位代码", "招考人数", "专业", "学历", "学位", "政治面貌", "基层工作最低年限", "工作地点", "咨询电话1"]


def record(department="001000", name="虚构甲机关", location="北京市", code="100110001001"):
    return [department, name, "虚构业务司", "一级主任科员", code, "2", "不限", "本科及以上", "学士", "不限", "二年", location, "010-00000000"]


def csv_bytes(rows):
    stream = io.StringIO()
    csv.writer(stream).writerows([HEADERS, *rows])
    return stream.getvalue().encode("utf-8-sig")


class NationalImportTests(unittest.TestCase):
    setUp = fixtures.AuditRegressions.setUp
    tearDown = fixtures.AuditRegressions.tearDown
    row = fixtures.AuditRegressions.row
    task = fixtures.AuditRegressions.task
    # Inherited fixtures keep database and uploaded files isolated.
    def test_national_headers_ignore_disclaimer_and_keep_department_code(self):
        rows = [["招考职位由招录机关编报，由招录机关负责解释。"], HEADERS, record()]
        mapped = map_fields(rows, "job_table")
        self.assertEqual(mapped.header_row_index, 1)
        self.assertEqual(mapped.column_indexes["department_code"], 0)
        self.assertEqual(mapped.column_indexes["unit_name"], 2)
        self.assertEqual(mapped.column_indexes["department_name"], 1)
        self.assertEqual(mapped.column_indexes["grassroots_requirement"], 10)

    def test_same_position_code_in_different_departments_is_not_duplicate(self):
        task = import_service.process_uploaded_file(file_name="中央机关及其直属机构2027年度招考简章.csv", content=csv_bytes([record(), record("002000", "虚构乙机关")]), year=2027)
        self.assertEqual(task["valid_rows"], 2)
        self.assertEqual(task["warning_rows"], 0)
        result = import_store.confirm_task(task["task_id"])
        self.assertEqual(result["inserted_count"], 2)
        jobs = repo.list_imported_jobs(limit=100)
        self.assertEqual({j["department_code"] for j in jobs}, {"001000", "002000"})
        self.assertEqual({j["unit_name"] for j in jobs}, {"虚构业务司"})

    def test_national_work_locations_determine_row_provinces(self):
        task = import_service.process_uploaded_file(file_name="中央机关及其直属机构2027年度招考简章.csv", content=csv_bytes([record(location="北京市"), record("002000", location="广西壮族自治区南宁市")]), province="neimenggu", exam_type="国考", year=2027)
        self.assertEqual(task["province"], "全国")
        self.assertEqual(task["exam_type"], "国考")
        self.assertEqual({r["province"] for r in task["valid_cleaned_rows"]}, {"beijing", "guangxi"})
        self.assertEqual(task["valid_cleaned_rows"][1]["region"], "广西壮族自治区南宁市")

    def test_national_same_code_lookup_requires_disambiguation(self):
        task = import_service.process_uploaded_file(file_name="中央机关2027年度招考简章.csv", content=csv_bytes([record("100110"), record("002000", "虚构乙机关")]), year=2027)
        import_store.confirm_task(task["task_id"])
        result = job_tool.find_single_job(target="公务员", exam_type="国考", region="北京", query="100110001001", year=2027)
        self.assertEqual(result["status"], "ambiguous")
        specific = job_tool.find_single_job(target="公务员", exam_type="国考", region="北京", query="虚构乙机关 100110001001", year=2027)
        self.assertEqual(specific["job"]["department_code"], "002000")
        jobs = job_tool.search_jobs(target="公务员", exam_type="国考", region="北京", education="本科", major="计算机", identity="不限", year=2027)
        self.assertEqual(len({j["job_id"] for j in jobs}), 2)

    def test_national_multi_sheet_merges_jobs_without_dropping_blank_sheet(self):
        tables = [RawTable("中央党群机关", [["招考职位由招录机关编报，由招录机关负责解释。"], HEADERS, record()], "fiction.xls"),
                  RawTable("中央行政机关", [HEADERS, record("002000", "虚构乙机关", "广西壮族自治区南宁市")], "fiction.xls")]
        with patch.object(import_service, "read_import_file", return_value=tables):
            task = import_service.build_import_task(Path("中央机关2027年度招考简章.xls"))
        self.assertEqual(task["total_rows"], 2)
        self.assertEqual(task["valid_rows"], 2)
        self.assertEqual(task["exam_type"], "国考")


    def test_national_scores_require_matching_department(self):
        from app.tools.score_tool import attach_scores_to_jobs
        job = dict(target="公务员", exam_type="国考", region="北京", year=2027, department_code="001000", position_code="100110001001")
        scores = [dict(target="公务员", exam_type="国考", region="北京", year=2027, department_code=department, position_code="100110001001", min_score=score) for department,score in [("002000",150),("001000",120)]]
        attach_scores_to_jobs([job],scores)
        self.assertEqual(job["min_score"],120)


    def test_national_candidate_samples_do_not_mix_departments(self):
        rows = [self.row(province="beijing", year=2027, exam_type="国考", job_code="100110001001", department_code=department, candidate_no=number, written_score=score) for department,number,score in [("001000","FAKE0001",120),("002000","FAKE0002",150)]]
        task = self.task(rows, "candidate_score_table")
        repo.confirm_import_task_data(task)
        summary = repo.get_candidate_score_summary_by_job(province="北京", year=2027, exam_type="国考", job_code="100110001001", department_code="001000")
        self.assertEqual(summary["sample_count"],1)
        self.assertEqual(summary["written_score_min"],120)


    def test_multi_province_work_locations_are_not_assigned_to_first_province(self):
        from app.imports.cleaners import _national_job_province
        self.assertEqual(_national_job_province("北京市、上海市"), "全国")
        self.assertEqual(_national_job_province("青海省海南藏族自治州"), "qinghai")

    def test_national_candidate_keys_without_numbers_include_department(self):
        from app.imports.cleaners import build_row_key
        row = self.row(exam_type="国考", department_code="001000", candidate_name="虚构甲", written_score=120, total_score=120, rank=1)
        for kind in ["candidate_score_table", "review_candidate_list"]:
            self.assertNotEqual(build_row_key(kind, row), build_row_key(kind, {**row,"department_code":"002000"}))

    def test_national_score_only_code_lookup_rejects_department_ambiguity(self):
        from app.tools import score_tool
        rows = [dict(target="公务员", exam_type="国考", region="北京", year=2027, department_code=department, position_code="100110001001", min_score=score) for department,score in [("002000",150),("001000",120)]]
        with patch.object(score_tool,"_read_scores",return_value=rows):
            result = score_tool.find_single_score_reference(target="公务员", exam_type="国考", region="北京", query="100110001001",year=2027)
            self.assertEqual(result["status"], "ambiguous")
            exact = score_tool.find_single_score_reference(target="公务员", exam_type="国考", region="北京", query="100110001001",year=2027,department_code="001000")
            self.assertEqual(exact["score"]["min_score"],120)


    def test_data_catalog_year_follows_imported_jobs(self):
        from app.tools.data_catalog import get_data_catalog
        task = import_service.process_uploaded_file(file_name="中央机关2027年度招考简章.csv",content=csv_bytes([record()]),year=2027)
        import_store.confirm_task(task["task_id"])
        self.assertEqual(get_data_catalog()["default_year"],2027)
