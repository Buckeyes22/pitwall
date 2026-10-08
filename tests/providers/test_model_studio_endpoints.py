"""Model Studio endpoint rules: pairing, regions, eligibility, derivation, classification."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path

from pitwall.providers.model_studio import catalog as ms

ROOT = Path(__file__).resolve().parents[2]

TOKEN = "https://token-plan.ap-southeast-1.maas.aliyuncs.com"


def personal(**extra: object) -> dict[str, object]:
    return {
        "kind": "model-studio",
        "plan": "token-plan-personal",
        "tier": "pro",
        "apiKeyEnv": "MODEL_STUDIO_API_KEY",
        **extra,
    }


class EndpointValidationTests(unittest.TestCase):
    def test_token_plan_defaults_region_protocol_and_concurrency(self) -> None:
        normalized = ms.validate_endpoint(personal(), "endpoints.ms")
        self.assertEqual("ap-southeast-1", normalized["region"])
        self.assertEqual("openai", normalized["protocol"])
        self.assertEqual(8, normalized["concurrency"])
        self.assertEqual(f"{TOKEN}/compatible-mode/v1", normalized["baseUrl"])

    def test_anthropic_protocol_derives_the_anthropic_base(self) -> None:
        normalized = ms.validate_endpoint(personal(protocol="anthropic"), "endpoints.ms")
        self.assertEqual(f"{TOKEN}/apps/anthropic", normalized["baseUrl"])

    def test_token_plan_outside_singapore_is_refused(self) -> None:
        with self.assertRaises(ms.ModelStudioConfigError) as caught:
            ms.validate_endpoint(personal(region="us-east-1"), "endpoints.ms")
        self.assertEqual("region_not_allowed", caught.exception.code)

    def test_token_plan_requires_a_tier(self) -> None:
        raw = personal()
        del raw["tier"]
        with self.assertRaises(ms.ModelStudioConfigError):
            ms.validate_endpoint(raw, "endpoints.ms")

    def test_pay_as_you_go_requires_a_known_region(self) -> None:
        with self.assertRaises(ms.ModelStudioConfigError) as caught:
            ms.validate_endpoint(
                {"kind": "model-studio", "plan": "pay-as-you-go", "apiKeyEnv": "K"}, "e"
            )
        self.assertEqual("unknown_region", caught.exception.code)

    def test_pay_as_you_go_prefers_the_workspace_host(self) -> None:
        normalized = ms.validate_endpoint(
            {
                "kind": "model-studio",
                "plan": "pay-as-you-go",
                "region": "ap-southeast-1",
                "workspace": "ws-1a2b",
                "apiKeyEnv": "K",
            },
            "e",
        )
        self.assertEqual(
            "https://ws-1a2b.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
            normalized["baseUrl"],
        )
        self.assertNotIn("concurrency", normalized)

    def test_region_without_a_shared_host_requires_a_workspace(self) -> None:
        with self.assertRaises(ms.ModelStudioConfigError) as caught:
            ms.validate_endpoint(
                {
                    "kind": "model-studio",
                    "plan": "pay-as-you-go",
                    "region": "eu-central-1",
                    "apiKeyEnv": "K",
                },
                "e",
            )
        self.assertEqual("workspace_required", caught.exception.code)

    def test_a_stored_base_url_must_equal_the_derived_one(self) -> None:
        ms.validate_endpoint(personal(baseUrl=f"{TOKEN}/compatible-mode/v1"), "e")
        with self.assertRaises(ms.ModelStudioConfigError) as caught:
            ms.validate_endpoint(
                personal(baseUrl="https://dashscope-intl.aliyuncs.com/compatible-mode/v1"), "e"
            )
        self.assertEqual("invalid_endpoint_pairing", caught.exception.code)

    def test_renews_on_is_a_date_and_only_for_token_plan(self) -> None:
        self.assertEqual(
            "2026-09-12", ms.validate_endpoint(personal(renewsOn="2026-09-12"), "e")["renewsOn"]
        )
        with self.assertRaises(ms.ModelStudioConfigError):
            ms.validate_endpoint(personal(renewsOn=12), "e")

    def test_automation_acceptance_is_recorded_only_as_accept(self) -> None:
        self.assertTrue(
            ms.endpoint_automation_accepted(
                ms.validate_endpoint(personal(tokenPlanAutomation="accept"), "e")
            )
        )
        self.assertFalse(ms.endpoint_automation_accepted(ms.validate_endpoint(personal(), "e")))
        with self.assertRaises(ms.ModelStudioConfigError):
            ms.validate_endpoint(personal(tokenPlanAutomation="yes"), "e")


class RuleTests(unittest.TestCase):
    def test_token_plan_key_pairs_only_with_token_plan(self) -> None:
        ms.check_key("token-plan-personal", "sk-sp-example")
        for plan, key in (
            ("token-plan-personal", "sk-example"),
            ("pay-as-you-go", "sk-sp-example"),
        ):
            with self.assertRaises(ms.ModelStudioConfigError) as caught:
                ms.check_key(plan, key)
            self.assertEqual("key_plan_mismatch", caught.exception.code)
            self.assertNotIn("example", str(caught.exception))

    def test_team_only_model_is_refused_on_personal(self) -> None:
        with self.assertRaises(ms.ModelStudioConfigError) as caught:
            ms.check_model("token-plan-personal", "kimi-k2.7-code")
        self.assertEqual("model_not_eligible", caught.exception.code)
        ms.check_model("token-plan-team", "kimi-k2.7-code")

    def test_unknown_and_non_text_models_are_refused(self) -> None:
        with self.assertRaises(ms.ModelStudioConfigError) as caught:
            ms.check_model("token-plan-personal", "qwen9-imaginary")
        self.assertEqual("model_not_in_catalog", caught.exception.code)
        with self.assertRaises(ms.ModelStudioConfigError) as caught:
            ms.check_model("token-plan-personal", "wan2.7-image")
        self.assertEqual("model_not_text", caught.exception.code)

    def test_router_model_is_token_plan_only(self) -> None:
        ms.check_model("token-plan-personal", "auto")
        with self.assertRaises(ms.ModelStudioConfigError):
            ms.check_model("pay-as-you-go", "auto")

    def test_limits_and_effort_come_from_the_catalog(self) -> None:
        self.assertEqual({"context": 991808, "output": 131072}, ms.route_limits("qwen3.8-flash"))
        self.assertIsNone(ms.route_limits("auto"))
        self.assertEqual(("xhigh", "medium", "low"), ms.effort_values("qwen3.8-max"))
        self.assertEqual((), ms.effort_values("glm-5.2"))


class ClassificationTests(unittest.TestCase):
    def test_code_wins_before_message(self) -> None:
        body = '{"code": "Throttling.AllocationQuota", "message": "Allocated quota exceeded"}'
        self.assertEqual(("rate_limit", 60), ms.classify_error(429, body))

    def test_openai_shaped_token_plan_exhaustion(self) -> None:
        body = '{"error": {"code": "insufficient_quota", "message": "Your token-plan quota has been exhausted."}}'
        self.assertEqual(("credits_exhausted", None), ms.classify_error(429, body))

    def test_openai_shaped_allocated_quota_is_per_minute(self) -> None:
        body = '{"error": {"code": "insufficient_quota", "message": "Allocated quota exceeded, please increase your quota limit."}}'
        self.assertEqual(("rate_limit", 60), ms.classify_error(429, body))

    def test_billing_states_and_unknown_bodies(self) -> None:
        self.assertEqual(("billing_state", 3600), ms.classify_error(400, '{"code": "Arrearage"}'))
        self.assertIsNone(ms.classify_error(429, "not json at all"))


class RenewalTests(unittest.TestCase):
    def test_window_steps_in_thirty_day_cycles_from_the_anchor(self) -> None:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
        start, reset = ms.credits_window("2026-09-12", now)
        self.assertEqual(datetime(2026, 11, 11, tzinfo=UTC), start)
        self.assertEqual(datetime(2026, 12, 11, tzinfo=UTC), reset)

    def test_future_anchor_still_yields_the_current_window(self) -> None:
        now = datetime(2026, 9, 1, tzinfo=UTC)
        start, reset = ms.credits_window("2026-09-12", now)
        self.assertEqual(datetime(2026, 8, 13, tzinfo=UTC), start)
        self.assertEqual(datetime(2026, 9, 12, tzinfo=UTC), reset)


if __name__ == "__main__":
    unittest.main()
