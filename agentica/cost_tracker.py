# -*- coding: utf-8 -*-
"""
@description: CostTracker — per-run LLM cost accounting.

Tracks token usage and estimates USD cost for every API call within
a single agent run.  The instance is attached to RunResponse.cost_tracker
and can be printed via RunResponse.cost_summary.

Pricing table follows CC's cost-tracker.ts pattern: per-1M-token USD rates
for input / output / cache_read / cache_write.  Unknown models are flagged
rather than silently ignored.

Usage::

    response = agent.run("...")
    print(response.cost_summary)
    print(f"total: ${response.total_cost_usd:.4f}")
"""
from dataclasses import dataclass, field
from typing import Dict

# ---------------------------------------------------------------------------
# Pricing table — USD per 1 M tokens
# Format: {"input": float, "output": float, "cache_read": float, "cache_write": float}
# ---------------------------------------------------------------------------
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # OpenAI
    "gpt-4o":               {"input": 2.50,  "output": 10.00, "cache_read": 1.25,  "cache_write": 2.50},
    "gpt-4o-mini":          {"input": 0.15,  "output": 0.60,  "cache_read": 0.075, "cache_write": 0.15},
    "gpt-4-turbo":          {"input": 10.00, "output": 30.00, "cache_read": 0.00,  "cache_write": 0.00},
    "gpt-4":                {"input": 30.00, "output": 60.00, "cache_read": 0.00,  "cache_write": 0.00},
    "gpt-3.5-turbo":        {"input": 0.50,  "output": 1.50,  "cache_read": 0.00,  "cache_write": 0.00},
    "o1":                   {"input": 15.00, "output": 60.00, "cache_read": 7.50,  "cache_write": 0.00},
    "o1-mini":              {"input": 1.10,  "output": 4.40,  "cache_read": 0.55,  "cache_write": 0.00},
    "o3-mini":              {"input": 1.10,  "output": 4.40,  "cache_read": 0.55,  "cache_write": 0.00},
    # Anthropic
    "claude-opus-4":            {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75},
    "claude-sonnet-4-20250514": {"input": 3.00,  "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-3-5":         {"input": 0.80,  "output": 4.00,  "cache_read": 0.08, "cache_write": 1.00},
    "claude-3-5-sonnet":        {"input": 3.00,  "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-3-haiku":           {"input": 0.25,  "output": 1.25,  "cache_read": 0.03, "cache_write": 0.30},
    # DeepSeek
    "deepseek-chat":        {"input": 0.27,  "output": 1.10,  "cache_read": 0.07,  "cache_write": 0.27},
    "deepseek-reasoner":    {"input": 0.55,  "output": 2.19,  "cache_read": 0.14,  "cache_write": 0.55},
    # ZhipuAI
    "glm-4-flash":          {"input": 0.00,  "output": 0.00,  "cache_read": 0.00,  "cache_write": 0.00},
    "glm-4-air":            {"input": 0.14,  "output": 0.14,  "cache_read": 0.00,  "cache_write": 0.00},
    "glm-4":                {"input": 0.71,  "output": 0.71,  "cache_read": 0.00,  "cache_write": 0.00},
    # Qwen (Alibaba)
    "qwen-turbo":           {"input": 0.06,  "output": 0.18,  "cache_read": 0.00,  "cache_write": 0.00},
    "qwen-plus":            {"input": 0.40,  "output": 1.20,  "cache_read": 0.00,  "cache_write": 0.00},
    "qwen-max":             {"input": 2.40,  "output": 9.60,  "cache_read": 0.00,  "cache_write": 0.00},
    # Moonshot
    "moonshot-v1-8k":       {"input": 0.18,  "output": 0.18,  "cache_read": 0.00,  "cache_write": 0.00},
    "moonshot-v1-32k":      {"input": 0.35,  "output": 0.35,  "cache_read": 0.00,  "cache_write": 0.00},
    # Doubao (ByteDance)
    "doubao-pro-4k":        {"input": 0.11,  "output": 0.32,  "cache_read": 0.00,  "cache_write": 0.00},
    "doubao-lite-4k":       {"input": 0.04,  "output": 0.08,  "cache_read": 0.00,  "cache_write": 0.00},
    # Yi
    "yi-lightning":         {"input": 0.14,  "output": 0.14,  "cache_read": 0.00,  "cache_write": 0.00},
    # Groq (fast inference)
    "llama3-70b-8192":      {"input": 0.59,  "output": 0.79,  "cache_read": 0.00,  "cache_write": 0.00},
    "mixtral-8x7b-32768":   {"input": 0.27,  "output": 0.27,  "cache_read": 0.00,  "cache_write": 0.00},
}


@dataclass
class ModelUsageStat:
    """Token usage statistics for a single model."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    requests: int = 0


@dataclass
class CostTracker:
    """Full-session cost tracker.

    One instance is created per Agent.run() call and attached to RunResponse.

    Attributes:
        total_cost_usd:      Accumulated USD cost across all API calls.
        total_input_tokens:  Accumulated input tokens.
        total_output_tokens: Accumulated output tokens.
        turns:               Number of API calls recorded.
        has_unknown_model:   True if any model was not in MODEL_PRICING.
        model_usage:         Per-model breakdown (keyed by normalised model id).
    """

    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    turns: int = 0
    has_unknown_model: bool = False
    model_usage: Dict[str, ModelUsageStat] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """Record one API call and return its cost in USD.

        Args:
            model_id:           Raw model identifier (may include provider prefix).
            input_tokens:       Prompt token count.
            output_tokens:      Completion token count.
            cache_read_tokens:  Cache-read token count (Anthropic prompt cache).
            cache_write_tokens: Cache-write token count.

        Returns:
            Cost of this single call in USD.
        """
        normalised = self._normalise(model_id)
        pricing = self._lookup_pricing(normalised)

        cost = (
            input_tokens       * pricing["input"]       / 1_000_000
            + output_tokens    * pricing["output"]      / 1_000_000
            + cache_read_tokens  * pricing["cache_read"]  / 1_000_000
            + cache_write_tokens * pricing["cache_write"] / 1_000_000
        )

        # Per-model stats
        stat = self.model_usage.setdefault(normalised, ModelUsageStat())
        stat.input_tokens        += input_tokens
        stat.output_tokens       += output_tokens
        stat.cache_read_tokens   += cache_read_tokens
        stat.cache_write_tokens  += cache_write_tokens
        stat.cost_usd            += cost
        stat.requests            += 1

        # Totals
        self.total_cost_usd      += cost
        self.total_input_tokens  += input_tokens
        self.total_output_tokens += output_tokens
        self.turns               += 1

        return cost

    def summary(self) -> str:
        """Return a human-readable cost summary (mirrors CC's formatTotalCost)."""
        lines = [f"Total cost:   ${self.total_cost_usd:.4f}"]
        if self.has_unknown_model:
            lines.append("              ⚠ unknown model(s) — costs may be underestimated")
        lines.append(
            f"Total tokens: {self.total_input_tokens:,} input"
            f" + {self.total_output_tokens:,} output"
        )
        lines.append(f"API calls:    {self.turns}")
        if self.model_usage:
            lines.append("Usage by model:")
            for model, stat in self.model_usage.items():
                parts = [f"{stat.input_tokens:,} in", f"{stat.output_tokens:,} out"]
                if stat.cache_read_tokens:
                    parts.append(f"{stat.cache_read_tokens:,} cache_read")
                if stat.cache_write_tokens:
                    parts.append(f"{stat.cache_write_tokens:,} cache_write")
                lines.append(f"  {model}: {', '.join(parts)} (${stat.cost_usd:.4f})")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _lookup_pricing(self, normalised_id: str) -> Dict[str, float]:
        """Look up pricing; fall back to prefix match, then zero with warning flag."""
        if normalised_id in MODEL_PRICING:
            return MODEL_PRICING[normalised_id]

        # Prefix match — e.g. "gpt-4o-2024-11-20" → "gpt-4o"
        for key in MODEL_PRICING:
            if normalised_id.startswith(key):
                return MODEL_PRICING[key]

        # Best-effort family match (first dash-separated segment)
        family = normalised_id.split("-")[0]
        for key in MODEL_PRICING:
            if key.split("-")[0] == family:
                return MODEL_PRICING[key]

        self.has_unknown_model = True
        return {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}

    @staticmethod
    def _normalise(model_id: str) -> str:
        """Strip provider prefixes and lowercase the model identifier."""
        _PREFIXES = (
            "openai/",
            "anthropic/",
            "accounts/fireworks/models/",
            "together_ai/",
            "groq/",
            "cohere/",
        )
        lower = model_id.lower().strip()
        for prefix in _PREFIXES:
            if lower.startswith(prefix):
                lower = lower[len(prefix):]
                break
        return lower
