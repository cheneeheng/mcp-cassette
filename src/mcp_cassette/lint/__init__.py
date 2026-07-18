"""Heuristic security lint over recorded cassettes.

Read-only: a cassette is never mutated or annotated. These are pattern rules, not a
guarantee — a clean lint is absence of *known* smells, nothing more.
"""

from __future__ import annotations

from .engine import run, run_with_notes
from .rules import LintFinding, LintReport

__all__ = ["LintFinding", "LintReport", "run", "run_with_notes"]
