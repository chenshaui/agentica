# -*- coding: utf-8 -*-
"""Tests for agentica.cost_tracker — per-run LLM cost accounting."""
import unittest

from agentica.cost_tracker import CostTracker, ModelUsageStat, MODEL_PRICING


class TestCostTrackerNormalise(unittest.TestCase):
    """_normalise strips provider prefixes and lowercases."""

    def test_openai_prefix(self):
        self.assertEqual(CostTracker._normalise("openai/gpt-4o"), "gpt-4o")

    def test_anthropic_prefix(self):
        self.assertEqual(CostTracker._normalise("anthropic/claude-3-haiku"), "claude-3-haiku")

    def test_groq_prefix(self):
        self.assertEqual(CostTracker._normalise("groq/llama3-70b-8192"), "llama3-70b-8192")

    def test_together_ai_prefix(self):
        self.assertEqual(CostTracker._normalise("together_ai/mixtral"), "mixtral")

    def test_no_prefix(self):
        self.assertEqual(CostTracker._normalise("gpt-4o-mini"), "gpt-4o-mini")

    def test_uppercase_lowered(self):
        self.assertEqual(CostTracker._normalise("GPT-4O"), "gpt-4o")

    def test_whitespace_stripped(self):
        self.assertEqual(CostTracker._normalise("  gpt-4o  "), "gpt-4o")


class TestCostTrackerLookupPricing(unittest.TestCase):
    """_lookup_pricing: exact match → prefix match → family match → zero."""

    def test_exact_match(self):
        ct = CostTracker()
        pricing = ct._lookup_pricing("gpt-4o-mini")
        self.assertEqual(pricing, MODEL_PRICING["gpt-4o-mini"])

    def test_prefix_match(self):
        ct = CostTracker()
        pricing = ct._lookup_pricing("gpt-4o-2024-11-20")
        self.assertEqual(pricing, MODEL_PRICING["gpt-4o"])

    def test_family_match(self):
        ct = CostTracker()
        # "claude-unknown-version" → family "claude" matches "claude-opus-4"
        pricing = ct._lookup_pricing("claude-unknown-version")
        self.assertIn("input", pricing)
        self.assertGreater(pricing["input"], 0)

    def test_unknown_model_returns_zero(self):
        ct = CostTracker()
        pricing = ct._lookup_pricing("totally-unknown-model-xyz")
        self.assertEqual(pricing["input"], 0.0)
        self.assertEqual(pricing["output"], 0.0)
        self.assertTrue(ct.has_unknown_model)


class TestCostTrackerRecord(unittest.TestCase):
    """record() calculates cost and accumulates stats."""

    def test_record_returns_cost(self):
        ct = CostTracker()
        cost = ct.record("gpt-4o-mini", input_tokens=1000, output_tokens=500)
        # gpt-4o-mini: input 0.15/M, output 0.60/M
        expected = 1000 * 0.15 / 1_000_000 + 500 * 0.60 / 1_000_000
        self.assertAlmostEqual(cost, expected, places=8)

    def test_record_accumulates_totals(self):
        ct = CostTracker()
        ct.record("gpt-4o-mini", input_tokens=100, output_tokens=50)
        ct.record("gpt-4o-mini", input_tokens=200, output_tokens=100)
        self.assertEqual(ct.total_input_tokens, 300)
        self.assertEqual(ct.total_output_tokens, 150)
        self.assertEqual(ct.turns, 2)
        self.assertGreater(ct.total_cost_usd, 0)

    def test_record_with_cache_tokens(self):
        ct = CostTracker()
        cost = ct.record("gpt-4o", input_tokens=100, output_tokens=50,
                         cache_read_tokens=200, cache_write_tokens=50)
        self.assertGreater(cost, 0)
        stat = ct.model_usage["gpt-4o"]
        self.assertEqual(stat.cache_read_tokens, 200)
        self.assertEqual(stat.cache_write_tokens, 50)

    def test_record_per_model_breakdown(self):
        ct = CostTracker()
        ct.record("gpt-4o-mini", input_tokens=100, output_tokens=50)
        ct.record("gpt-4o", input_tokens=100, output_tokens=50)
        self.assertIn("gpt-4o-mini", ct.model_usage)
        self.assertIn("gpt-4o", ct.model_usage)
        self.assertEqual(ct.model_usage["gpt-4o-mini"].requests, 1)
        self.assertEqual(ct.model_usage["gpt-4o"].requests, 1)

    def test_record_unknown_model(self):
        ct = CostTracker()
        cost = ct.record("unknown-model", input_tokens=1000, output_tokens=500)
        self.assertEqual(cost, 0.0)
        self.assertTrue(ct.has_unknown_model)


class TestCostTrackerSummary(unittest.TestCase):
    """summary() generates human-readable output."""

    def test_summary_format(self):
        ct = CostTracker()
        ct.record("gpt-4o-mini", input_tokens=1000, output_tokens=500)
        s = ct.summary()
        self.assertIn("Total cost:", s)
        self.assertIn("Total tokens:", s)
        self.assertIn("API calls:", s)
        self.assertIn("gpt-4o-mini", s)

    def test_summary_unknown_model_warning(self):
        ct = CostTracker()
        ct.record("unknown-xyz", input_tokens=100, output_tokens=50)
        s = ct.summary()
        self.assertIn("unknown model", s)

    def test_summary_empty(self):
        ct = CostTracker()
        s = ct.summary()
        self.assertIn("$0.0000", s)


class TestModelUsageStat(unittest.TestCase):
    """ModelUsageStat defaults."""

    def test_defaults(self):
        stat = ModelUsageStat()
        self.assertEqual(stat.input_tokens, 0)
        self.assertEqual(stat.output_tokens, 0)
        self.assertEqual(stat.cost_usd, 0.0)
        self.assertEqual(stat.requests, 0)


if __name__ == "__main__":
    unittest.main()
