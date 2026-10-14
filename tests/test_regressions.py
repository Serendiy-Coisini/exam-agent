"""Fictional, isolated fixtures for the 2026-10 project audit."""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.db import repositories
from app.db.database import connection, initialize_database
from app.db import import_repository as repo
from app.imports import import_store
from app.imports.cleaners import build_row_key
from app.services import import_service, auth_service
from app.tools import job_tool, score_tool, region_resolver
from app.main import app


async def request(method, path, payload=None, token=None):
    messages = []
    body = json.dumps(payload or {}, ensure_ascii=False).encode()
    headers = [(b"content-type", b"application/json")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
               "scheme": "http", "method": method, "path": path, "raw_path": path.encode(),
               "query_string": b"", "headers": headers, "client": ("127.0.0.1", 10),
               "server": ("audit", 80), "root_path": ""}, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(raw) if raw else None


class AuditRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="exam_regression_")
        root = Path(self.temp.name)
        self.patches = [patch.object(settings, "DATABASE_URL", "sqlite:///" + str(root / "test.db")),
                        patch.object(settings, "AUTH_ENABLED", False),
                        patch.object(settings, "DEV_LOGIN_ENABLED", False),
                        patch.object(settings, "LLM_API_KEY", ""),
                        patch.object(settings, "LLM_MODEL_NAME", ""),
                        patch.object(settings, "RAG_ENABLED", False),
                        patch.object(import_store, "IMPORT_TASK_DIR", root / "imports"),
                        patch.object(import_store, "CLEANED_DATA_DIR", root / "cleaned")]
        for p in self.patches:
            p.start()
        initialize_database()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def row(self, **overrides):
        return dict(year=2026, province="guangxi", region="南宁", exam_type="省考",
                    job_code="12345678", unit_name="虚构单位", job_name="虚构岗位",
                    education_requirement="本科及以上", major_requirement="不限",
                    identity_requirement="不限", recruit_count=2,
                    source_file="fiction.csv", **overrides) if not overrides else {
                        **self.row(), **overrides}

    def task(self, rows=None, kind="job_table", task_id="import_fixture"):
        task = dict(task_id=task_id, detected_type=kind, detected_type_label="测试",
                    file_name="fiction.csv", province="guangxi", year=2026, exam_type="省考",
                    valid_cleaned_rows=rows or [self.row()], preview_rows=[], status="PREVIEW_READY",
                    valid_rows=len(rows or [self.row()]), total_rows=len(rows or [self.row()]),
                    warning_rows=0, warnings=[], confidence=1, source_kind="imported", storage="database")
        import_store.save_task(task)
        return task

    def test_auth_enabled_rejects_anonymous_import_mutation_and_analysis(self):
        settings.AUTH_ENABLED = True
        for method, path, payload in [("GET", "/api/v1/imports/templates", None),
                                      ("DELETE", "/api/v1/imports/import_fixture", None),
                                      ("POST", "/api/v1/agent/analyze", {"question": "你好"})]:
            with self.subTest(path=path):
                self.assertEqual(asyncio.run(request(method, path, payload))[0], 401)

    def test_local_mode_still_allows_imports(self):
        self.assertEqual(asyncio.run(request("GET", "/api/v1/imports/templates"))[0], 200)

    def test_dev_login_rejects_password_account(self):
        auth_service.register_user(username="victim", email="", password="secret123")
        with self.assertRaises(ValueError):
            auth_service.dev_login("victim")

    def test_dev_login_cannot_reactivate_account(self):
        user = repositories.upsert_dev_user("disabled")
        with connection() as db:
            db.execute("UPDATE users SET is_active=0, status='disabled' WHERE id=?", (user["id"],))
        with self.assertRaises(ValueError):
            auth_service.dev_login("disabled")

    def test_formal_auth_disables_dev_endpoint_even_if_flag_is_true(self):
        settings.AUTH_ENABLED = True
        settings.DEV_LOGIN_ENABLED = True
        self.assertEqual(asyncio.run(request("POST", "/api/v1/auth/dev-login", {"username": "demo"}))[0], 403)

    def test_delete_only_selected_year(self):
        task = self.task([self.row(year=2025), self.row(year=2026)])
        repo.confirm_import_task_data(task)
        selected = next(d for d in repo.list_imported_dataset_summaries() if d["year"] == 2025)
        result = import_service.delete_import_dataset(selected["dataset_id"])
        self.assertEqual(result["deleted_rows"], 1)
        self.assertEqual([d["year"] for d in repo.list_imported_dataset_summaries()], [2026])
        with self.assertRaises(FileNotFoundError):
            import_store.load_task(task["task_id"])

    def test_deleted_task_is_not_readable_and_upload_is_removed(self):
        task = self.task()
        upload = import_store.save_uploaded_file(task["task_id"], "fiction.csv", b"fiction")
        task["upload_path"] = str(upload)
        import_store.save_task(task)
        import_service.delete_import_task(task["task_id"])
        self.assertFalse(upload.exists())
        with self.assertRaises(FileNotFoundError):
            import_store.load_task(task["task_id"])

    def test_preview_removes_identity_from_keys_and_warnings_recursively(self):
        key = build_row_key("review_candidate_list", self.row(candidate_no="FAKE12345"))
        row = {"row_key": key, "candidate_no": "FAKE12345", "written_score": 100}
        warning = {"message": "重复记录", "raw_value": key, "field": "row_key"}
        preview = import_service.public_preview({"detected_type": "review_candidate_list",
                    "preview_rows": [row], "warnings": [warning], "warning_summary": [warning],
                    "import_report": {"warnings": [warning]},
                    "outputs": [{"dataset_type": "review_candidate_list", "preview_rows": [row], "warnings": [warning]}]})
        self.assertNotIn("FAKE12345", json.dumps(preview))
        self.assertEqual(preview["preview_rows"][0]["written_score"], 100)

    def test_signup_association_respects_year_and_exam(self):
        task = self.task([self.row(year=2025), self.row(year=2026)])
        repo.confirm_import_task_data(task)
        signup = self.task([self.row(year=2025, signup_count=999)], "signup_table", "import_signup")
        repo.confirm_import_task_data(signup)
        jobs = job_tool._read_jobs_from_database(region="广西")
        self.assertIsNone(next(j for j in jobs if j["year"] == 2026)["applicants_count"])
        self.assertEqual(next(j for j in jobs if j["year"] == 2025)["applicants_count"], 999)

    def test_historical_score_is_not_exact_current_year(self):
        job = dict(target="公务员", exam_type="省考", region="广西", year=2026, position_code="12345678", recruit_count=2)
        score = dict(target="公务员", exam_type="省考", region="广西", year=2024, position_code="12345678", min_score=99, recruit_count=10)
        score_tool.attach_scores_to_jobs([job], [score])
        self.assertNotEqual(job["score_match_type"], "exact_position_code")
        self.assertEqual(job["recruit_count"], 2)

    def test_same_year_score_never_overwrites_current_headcount(self):
        job = dict(target="公务员", exam_type="省考", region="广西", year=2026, position_code="12345678", recruit_count=2)
        score = dict(target="公务员", exam_type="省考", region="广西", year=2026, position_code="12345678", min_score=99, recruit_count=10)
        score_tool.attach_scores_to_jobs([job], [score])
        self.assertEqual(job["min_score"], 99)
        self.assertEqual(job["recruit_count"], 2)

    def test_changed_reimport_updates_and_identical_reimport_skips(self):
        repo.confirm_import_task_data(self.task())
        result = repo.confirm_import_task_data(self.task([self.row(recruit_count=8)], task_id="import_update"))
        self.assertEqual(result["updated_count"], 1)
        with connection() as db:
            self.assertEqual(db.execute("SELECT recruit_count FROM exam_jobs").fetchone()[0], 8)
        again = repo.confirm_import_task_data(self.task([self.row(recruit_count=8)], task_id="import_again"))
        self.assertEqual(again["updated_count"], 0)
        self.assertEqual(again["skipped_count"], 1)

    def test_update_only_confirmation_is_success_and_idempotent(self):
        repo.confirm_import_task_data(self.task())
        updated = self.task([self.row(recruit_count=8)], task_id="import_update")
        result = import_store.confirm_task(updated["task_id"])
        self.assertEqual(result["status"], "IMPORTED")
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(import_store.confirm_task(updated["task_id"])["updated_count"], 1)

    def test_institution_target(self):
        self.assertEqual(job_tool._normalize_imported_job_row(self.row(exam_type="事业单位"))["target"], "事业单位")

    def test_education_alternatives_and_minimum(self):
        self.assertGreater(job_tool._score_education("本科", "本科、硕士研究生"), 0)
        self.assertEqual(job_tool._score_education("硕士", "本科"), 0)
        self.assertGreater(job_tool._score_education("硕士", "本科及以上"), 0)

    def test_empty_major_can_match_unrestricted_job(self):
        self.assertGreater(job_tool._score_major("", "不限"), 0)

    def test_all_provinces_available_without_data(self):
        options = region_resolver.get_region_options("广西")
        self.assertIn("广西", options["provinces"])
        self.assertIn("广东", options["provinces"])
        self.assertGreaterEqual(len(options["provinces"]), 32)

    def test_explicit_year_and_known_qualification_conflicts(self):
        repo.confirm_import_task_data(self.task([self.row(year=2025), self.row(year=2026, political_requirement="中共党员")]))
        result = job_tool.search_jobs(target="公务员", exam_type="省考", region="广西", education="本科", major="计算机", identity="不限", year=2026, profile={"political_status": "群众"})
        self.assertFalse([j for j in result if not j.get("data_error")])

    def test_upload_reads_at_most_limit_plus_one(self):
        from app.api.v1.imports import upload_import_file
        from app.services.import_service import MAX_UPLOAD_BYTES
        from fastapi import HTTPException
        class Upload:
            filename = "fiction.csv"
            requested = None
            async def read(self, size=-1):
                self.requested = size
                return b"x" * (MAX_UPLOAD_BYTES + 1)
        file = Upload()
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(upload_import_file(file, None, None, None))
        self.assertEqual(raised.exception.status_code, 413)
        self.assertEqual(file.requested, MAX_UPLOAD_BYTES + 1)

    def test_llm_timeout_and_retry_are_explicit(self):
        from app.services.llm_service import _build_client
        class Client:
            def __init__(self, **kwargs):
                self.options = kwargs
        client = _build_client(Client)
        self.assertGreater(client.options["timeout"], 0)
        self.assertLessEqual(client.options["timeout"], 120)
        self.assertEqual(client.options["max_retries"], 0)

    def test_search_defaults_to_latest_year_but_can_query_history(self):
        repo.confirm_import_task_data(self.task([self.row(year=2025), self.row(year=2026)]))
        args = dict(target="公务员", exam_type="省考", region="广西", education="本科", major="计算机", identity="不限")
        current = job_tool.search_jobs(**args)
        historical = job_tool.search_jobs(**args, year=2025)
        self.assertEqual([j["year"] for j in current], [2026])
        self.assertEqual([j["year"] for j in historical], [2025])

    def test_qualification_unknowns_are_pending_and_conflicts_excluded(self):
        repo.confirm_import_task_data(self.task([self.row(degree_requirement="学士", political_requirement="中共党员", grassroots_requirement="两年以上")]))
        args = dict(target="公务员", exam_type="省考", region="广西", education="本科", major="计算机", identity="不限")
        jobs = job_tool.search_jobs(**args)
        self.assertTrue(jobs)
        self.assertTrue(any("待核验" in note for note in jobs[0]["verify_notes"]))
        self.assertFalse(job_tool.search_jobs(**args, profile={"degree": "硕士", "political_status": "中共党员", "grassroots_years": 3}))
        self.assertFalse(job_tool.search_jobs(**args, profile={"degree": "学士", "political_status": "中共党员", "grassroots_years": 1}))

    def test_stored_profile_qualifications_reach_analysis_payload(self):
        from app.schemas.agent_schema import AgentAnalyzeRequest
        from app.services.product_service import apply_profile_defaults
        result = apply_profile_defaults(AgentAnalyzeRequest(question="推荐岗位"), {"degree": "学士", "political_status": "群众", "raw_profile_json": {"grassroots_years": 1}})
        self.assertEqual(result.profile["political_status"], "群众")
        self.assertEqual(result.profile["grassroots_years"], 1)

    def test_csv_upload_preserves_mixed_years_through_confirmation(self):
        content = "年份,职位代码,职位名称,招录单位,学历要求,专业要求,招录人数\n2025,12345678,虚构岗位,虚构单位,本科,不限,2\n2026,12345678,虚构岗位,虚构单位,本科,不限,3\n".encode()
        task = import_service.process_uploaded_file(file_name="fiction.csv", content=content, province="广西", exam_type="省考", year=2026)
        self.assertEqual({row["year"] for row in task["valid_cleaned_rows"]}, {2025, 2026})
        result = import_store.confirm_task(task["task_id"])
        self.assertEqual(result["inserted_count"], 2)
        self.assertEqual({d["year"] for d in repo.list_imported_dataset_summaries()}, {2025, 2026})

    def test_imported_major_catalog_participates_in_matching(self):
        repo.confirm_import_task_data(self.task([self.row(major_requirement="财政学类")]))
        major = dict(year=2026, province="guangxi", major_name="财政学", major_code="020201", major_category="财政学类", degree_level="本科")
        repo.confirm_import_task_data(self.task([major], "major_catalog", "import_major"))
        jobs = job_tool.search_jobs(target="公务员", exam_type="省考", region="广西", education="本科", major="财政学", identity="不限")
        self.assertTrue(jobs)
        self.assertEqual(jobs[0]["position_code"], "12345678")

    def test_candidate_corrections_update_without_cross_exam_replacement(self):
        first = self.row(candidate_no="FAKE0001", candidate_name="虚构甲", total_score=100, rank=1, source_sheet="Sheet1")
        repo.confirm_import_task_data(self.task([first], "candidate_score_table"))
        corrected = repo.confirm_import_task_data(self.task([{**first, "total_score": 110, "rank": 2}], "candidate_score_table", "import_correct"))
        self.assertEqual(corrected["updated_count"], 1)
        repo.confirm_import_task_data(self.task([{**first, "exam_type": "国考"}], "candidate_score_table", "import_other"))
        with connection() as db:
            rows = db.execute("SELECT exam_type,total_score FROM exam_candidate_scores ORDER BY exam_type").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["exam_type"]: r["total_score"] for r in rows}, {"省考": 110, "国考": 100})

    def test_known_non_party_political_conflict_is_excluded(self):
        matched, _ = job_tool._check_additional_qualifications({"political_requirement": "共青团员"}, {"political_status": "群众"})
        self.assertFalse(matched)

    def test_single_job_defaults_to_latest_year(self):
        repo.confirm_import_task_data(self.task([self.row(year=2025), self.row(year=2026)]))
        result = job_tool.find_single_job(target="公务员", exam_type="省考", region="广西", query="12345678")
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["job"]["year"], 2026)

    def test_major_catalog_loaded_once_per_scope(self):
        repo.confirm_import_task_data(self.task([self.row(job_code=str(12345678 + i)) for i in range(3)]))
        original = job_tool.list_imported_major_catalog
        with patch.object(job_tool, "list_imported_major_catalog", wraps=original) as calls:
            jobs = job_tool.search_jobs(target="公务员", exam_type="省考", region="广西", education="本科", major="计算机", identity="不限")
        self.assertEqual(len(jobs), 3)
        self.assertEqual(calls.call_count, 1)

    def test_legacy_candidate_key_is_updated_without_duplicate(self):
        first = self.row(candidate_no="FAKE0001", total_score=100, rank=1)
        repo.confirm_import_task_data(self.task([first], "candidate_score_table"))
        with connection() as db:
            db.execute("UPDATE exam_candidate_scores SET row_key='legacy-before-fix'")
        result = repo.confirm_import_task_data(self.task([{**first, "total_score": 110}], "candidate_score_table", "import_correct"))
        self.assertEqual(result["updated_count"], 1)
        with connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exam_candidate_scores").fetchone()[0], 1)

    def test_task_cleanup_never_removes_outside_file(self):
        task = self.task()
        outside = Path(self.temp.name) / "outside.csv"
        outside.write_text("keep")
        task["upload_path"] = str(outside)
        import_store.save_task(task)
        import_service.delete_import_task(task["task_id"])
        self.assertEqual(outside.read_text(), "keep")

    def test_analysis_does_not_block_event_loop(self):
        import app.api.v1.agent as api
        original = api.analyze_exam_request
        def slow(payload):
            time.sleep(0.15)
            return original(payload)
        async def check():
            async def heartbeat():
                start = time.perf_counter()
                await asyncio.sleep(0.01)
                return time.perf_counter() - start
            beat = asyncio.create_task(heartbeat())
            await asyncio.sleep(0)
            await request("POST", "/api/v1/agent/analyze", {"question": "你好"})
            return await beat
        with patch.object(api, "analyze_exam_request", slow):
            self.assertLess(asyncio.run(check()), 0.10)


if __name__ == "__main__":
    unittest.main()
