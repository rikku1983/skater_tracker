"""
Maps a raw event name to a canonical event_group and series using
the events_annotation_cleaned.csv as a training set.

Uses token-overlap scoring (stdlib only) — common sport-event words like
"short", "track", "championships" are treated as stop-words so that
distinctive location words (e.g. "great lakes", "gateway") drive the match.
"""
from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

ANNOTATION_CSV = Path(__file__).parents[2] / "data" / "events_annotation_cleaned.csv"
CONFIDENCE_THRESHOLD = 0.60

# Known token aliases applied before matching (both sides lowercased).
# Maps variant → canonical token.
_ALIASES: dict[str, str] = {
    "northburke": "northbrook",
    "mastc": "masa",
    "mast championships": "masa championships",
    "mast short track championships": "masa championships",
    "barrell": "barrel",
    "clause": "claus",
    "middle atlantic short track championships": "masa championships",
}


def _apply_aliases(text: str) -> str:
    for variant, canonical in _ALIASES.items():
        text = text.replace(variant, canonical)
    return text


# Strip leading year or date tokens like "2023 ", "2023-10-14 - ", "2024-01-15 "
_YEAR_PREFIX = re.compile(r'^\d{4}(?:-\d{1,2}-\d{1,2})?\s*[-–]?\s*')

# Words that appear in almost every event name and carry no discriminating signal.
_STOP_TOKENS: frozenset[str] = frozenset({
    "short", "track", "st", "championships", "championship",
    "open", "annual", "and", "the", "a", "an", "in", "of",
    "uss", "us", "usa", "united", "states", "american",
    "1", "2", "3", "4", "5", "6", "7", "8", "9",
})


def _clean(name: str) -> str:
    """Lowercase, strip year prefix, apply aliases, collapse whitespace."""
    s = name.lower().strip()
    s = _YEAR_PREFIX.sub("", s)
    s = _apply_aliases(s)
    return re.sub(r"\s+", " ", s).strip()


def _key_tokens(cleaned: str) -> frozenset[str]:
    """Extract non-stop word tokens from a cleaned event name."""
    tokens = frozenset(re.findall(r"[a-z]+", cleaned))
    return tokens - _STOP_TOKENS


def _token_score(candidate_tokens: frozenset[str], known_tokens: frozenset[str]) -> float:
    """F1-style token overlap: 2*|intersection| / (|candidate| + |known|)."""
    if not candidate_tokens or not known_tokens:
        return 0.0
    overlap = candidate_tokens & known_tokens
    return 2 * len(overlap) / (len(candidate_tokens) + len(known_tokens))


@lru_cache(maxsize=1)
def _load_training() -> list[tuple[frozenset[str], str, str]]:
    """Return list of (key_tokens, event_group, series)."""
    rows: list[tuple[frozenset[str], str, str]] = []
    if not ANNOTATION_CSV.exists():
        return rows
    with open(ANNOTATION_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_name = row.get("event_name", "").strip()
            group = row.get("event_group", "").strip()
            series = row.get("series", "").strip()
            if raw_name and group:
                tokens = _key_tokens(_clean(raw_name))
                if tokens:
                    rows.append((tokens, group, series))
    return rows


def normalize_event(event_name: str) -> tuple[Optional[str], Optional[str], float]:
    """
    Find the best-matching known event and return (event_group, series, confidence).

    confidence is a [0, 1] token-overlap F1 score.  Returns (None, None, score)
    when no match exceeds CONFIDENCE_THRESHOLD.
    """
    candidate_tokens = _key_tokens(_clean(event_name))
    training = _load_training()
    if not training or not candidate_tokens:
        return None, None, 0.0

    best_score = 0.0
    best_group: Optional[str] = None
    best_series: Optional[str] = None

    for known_tokens, group, series in training:
        score = _token_score(candidate_tokens, known_tokens)
        if score > best_score:
            best_score = score
            best_group = group
            best_series = series

    if best_score >= CONFIDENCE_THRESHOLD:
        return best_group, best_series, best_score
    return None, None, best_score
