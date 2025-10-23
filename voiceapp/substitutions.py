"""Utilities for applying user-defined transcription substitutions."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from .settings import config_dir

# Candidate filenames to check for substitution rules. Checked in order.
SUBSTITUTION_FILENAMES: Sequence[str] = (
    "substitutions.txt",
    "transcription_substitutions.txt",
)


def _candidate_paths() -> Iterable[Path]:
    """Yield possible locations for the substitution mapping file."""
    cwd = Path.cwd()
    for name in SUBSTITUTION_FILENAMES:
        yield cwd / name
    cfg_dir = config_dir()
    for name in SUBSTITUTION_FILENAMES:
        yield cfg_dir / name


def _split_sources(raw: str) -> List[str]:
    """Return the individual source tokens from the left-hand side."""
    parts = re.split(r"[|,]", raw)
    return [p.strip() for p in parts if p.strip()]


def _parse_line(line: str) -> Tuple[List[str], str] | None:
    """Parse a single line into a ([sources], target) mapping."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    # Support source -> target, source = target, and target <- source syntax.
    for separator in ("<-", "->", "="):
        if separator in stripped:
            left, right = stripped.split(separator, 1)
            left = left.strip()
            right = right.strip()
            if not left or not right:
                return None
            if separator == "<-":
                target = left
                sources = _split_sources(right)
            else:
                sources = _split_sources(left)
                target = right
            if not target or not sources:
                return None
            return sources, target
    return None


def _load_rules(path: Path) -> List[Tuple[List[str], str]]:
    rules: List[Tuple[List[str], str]] = []
    try:
        if path.exists() and path.is_file():
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                parsed = _parse_line(raw_line)
                if parsed:
                    rules.append(parsed)
    except Exception:
        # Silently ignore unreadable files; transcription should still work.
        return []
    return rules


def load_substitutions() -> List[Tuple[List[str], str]]:
    """Return substitution rules discovered in the candidate files."""
    rules: List[Tuple[List[str], str]] = []
    seen_paths: set[Path] = set()
    for path in _candidate_paths():
        if path in seen_paths:
            continue
        seen_paths.add(path)
        rules.extend(_load_rules(path))
    return rules


def apply_substitutions(text: str) -> str:
    """Apply user-defined substitutions to the provided text."""
    if not text:
        return text
    rules = load_substitutions()
    if not rules:
        return text
    updated = text
    for sources, target in rules:
        if not target:
            continue
        for source in sources:
            if not source:
                continue
            pattern = re.compile(re.escape(source), flags=re.IGNORECASE)
            updated = pattern.sub(target, updated)
    return updated
