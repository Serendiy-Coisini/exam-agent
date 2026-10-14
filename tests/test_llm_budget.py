"""Stage deadline and diagnostic tests use a fake clock and provider only."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import llm_service as service
from app.services.model_settings_service import ModelConfiguration


class LLMBudgetTests(unittest.TestCase):
    def setUp(self):
        self.config = ModelConfiguration("https://fiction.invalid/v1", "fiction-model", "fiction-key")
        self.config_patch = patch.object(service, "get_model_configuration", return_value=self.config)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.client = MagicMock()
        self.client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="可见模型答复"))])
        self.provider_patch = patch("openai.OpenAI", return_value=self.client)
        self.factory = self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)

    def test_optional_stage_reserves_time_for_final_report(self):
        self.assertTrue(hasattr(service, "model_stage"), "Missing stage-aware budgeting")
        with patch.object(service.time, "monotonic", return_value=100), service.inference_budget(30):
            with service.model_stage("candidate_selection", reserve_seconds=20):
                service.generate_llm_chat("system", [], "fallback")
                self.assertLessEqual(self.factory.call_args.kwargs["timeout"], 10)
            with service.model_stage("final_report"):
                result = service.generate_llm_chat("system", [], "fallback")
                self.assertLessEqual(self.factory.call_args.kwargs["timeout"], 30)
                self.assertEqual(result["model_metadata"]["stage"], "final_report")

    def test_reserved_budget_is_exhausted_before_total_budget(self):
        self.assertTrue(hasattr(service, "model_stage"), "Missing stage-aware budgeting")
        with patch.object(service.time, "monotonic", return_value=100), service.inference_budget(10), service.model_stage("card", reserve_seconds=10):
            result = service.generate_llm_chat("system", [], "fallback")
        self.assertEqual(result["failure_reason"], "budget_exhausted")
        self.assertFalse(result["llm_used"])
        self.factory.assert_not_called()

    def test_nested_budget_cannot_extend_parent_deadline_and_restores_context(self):
        self.assertTrue(hasattr(service, "budget_remaining"), "Missing budget inspection")
        with patch.object(service.time, "monotonic", return_value=100):
            with service.inference_budget(5):
                with service.inference_budget(50):
                    self.assertEqual(service.budget_remaining(), 5)
                self.assertEqual(service.budget_remaining(), 5)
            self.assertIsNone(service.budget_remaining())

    def test_deadline_failure_is_distinct_from_provider_failure(self):
        with patch.object(service.time, "monotonic", return_value=100), service.inference_budget(0):
            result = service.generate_llm_json("prompt", {"intent": "chat"})
        self.assertEqual(result.get("failure_reason"), "budget_exhausted")
        self.assertFalse(result["llm_used"])
        self.factory.assert_not_called()

    def test_provider_body_and_key_are_not_exposed(self):
        self.client.chat.completions.create.side_effect = RuntimeError("fiction-key sensitive provider body")
        result = service.generate_llm_chat("system", [], "fallback")
        self.assertEqual(result.get("failure_reason"), "provider_failure")
        self.assertNotIn("fiction-key", json.dumps(result))
        self.assertNotIn("sensitive provider body", json.dumps(result))
        self.assertTrue(result["model_metadata"]["attempted"])

    def test_missing_configuration_is_explicit(self):
        with patch.object(service, "get_model_configuration", return_value=ModelConfiguration("", "", "")):
            result = service.generate_llm_chat("system", [], "fallback")
        self.assertEqual(result.get("failure_reason"), "missing_configuration")
        self.factory.assert_not_called()

    def test_invalid_json_and_schema_have_distinct_reasons(self):
        for content, reason in (("broken JSON", "invalid_json"), ("[]", "invalid_schema"),
                                ('{"intent": []}', "invalid_schema"), ("{}", "invalid_schema")):
            with self.subTest(content=content):
                self.client.chat.completions.create.return_value.choices[0].message.content = content
                result = service.generate_llm_json("prompt", {"intent": "chat"})
                self.assertEqual(result.get("failure_reason"), reason)
                self.assertEqual(result["intent"], "chat")
                self.assertTrue(result["model_metadata"]["produced_text"])

    def test_success_reports_attempt_and_stage_without_secret(self):
        result = service.generate_llm_chat("system", [], "fallback")
        self.assertEqual(result.get("failure_reason"), "")
        self.assertTrue(result["llm_used"])
        self.assertTrue(result["model_metadata"]["produced_text"])
        self.assertEqual(result["model"], "fiction-model")
        self.assertNotIn("fiction-key", json.dumps(result))

    def test_late_optional_response_does_not_spend_the_final_report_reserve(self):
        self.assertTrue(hasattr(service, "model_stage"), "Missing stage-aware budgeting")
        now = [100.0]
        def slow_response(**kwargs):
            now[0] += 11
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="late text"))])
        self.client.chat.completions.create.side_effect = slow_response
        with patch.object(service.time, "monotonic", side_effect=lambda: now[0]), service.inference_budget(30):
            with service.model_stage("optional", reserve_seconds=20):
                result = service.generate_llm_chat("system", [], "fallback")
                self.assertEqual(result.get("failure_reason"), "budget_exhausted")
                self.assertTrue(result["model_metadata"]["produced_text"])
            self.assertEqual(service.budget_remaining(), 19)

    def test_partial_object_remains_compatible_with_legacy_card_mocks(self):
        self.client.chat.completions.create.return_value.choices[0].message.content = '{"why_recommended":"模型理由"}'
        result = service.generate_llm_json("prompt", {"why_recommended": "规则理由", "major_match": "规则核验"})
        self.assertTrue(result["llm_used"])
        self.assertEqual(result["why_recommended"], "模型理由")


if __name__ == "__main__":
    unittest.main()
