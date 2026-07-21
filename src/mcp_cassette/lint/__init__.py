"""Heuristic security lint over recorded cassettes.

Read-only: a cassette is never mutated or annotated. These are pattern rules, not a
guarantee — a clean lint is absence of *known* smells, nothing more.
"""

from __future__ import annotations

from .engine import run, run_with_notes
from .packs import (
    PatternRule,
    PatternSet,
    ProjectLintConfig,
    build_pattern_set,
    discover_config,
    load_pack,
)
from .rules import LintFinding, LintReport

__all__ = [
    "LintFinding",
    "LintReport",
    "PatternRule",
    "PatternSet",
    "ProjectLintConfig",
    "build_pattern_set",
    "discover_config",
    "load_pack",
    "run",
    "run_with_notes",
]
