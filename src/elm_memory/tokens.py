"""Model-neutral token accounting used by ELM retrieval surfaces."""
from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Return ELM's deterministic conservative-enough token estimate.

    This is deliberately not a tokenizer for any one model. Keeping one stable
    estimator makes budgets reproducible across agents and supported platforms.
    """

    return max(1, (len(text) + 3) // 4)
