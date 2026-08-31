"""Reproducible, read-only research tools for evaluating Thesis."""

from thesis.research.spec import (
    DEFAULT_SPEC_PATH,
    PINNED_STRATEGY_SPEC_SHA256,
    StrategySpecError,
    load_strategy_spec,
    strategy_spec_digest,
    verify_runtime_fingerprints,
)
from thesis.research.study import evaluate_dataset, evaluate_study_pair

__all__ = [
    "DEFAULT_SPEC_PATH",
    "PINNED_STRATEGY_SPEC_SHA256",
    "StrategySpecError",
    "evaluate_dataset",
    "evaluate_study_pair",
    "load_strategy_spec",
    "strategy_spec_digest",
    "verify_runtime_fingerprints",
]