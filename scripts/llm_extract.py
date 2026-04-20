"""
LLM-based race result extractor for unknown/non-standard PDF formats.

Usage:
    ANTHROPIC_API_KEY=sk-ant-... python scripts/llm_extract.py <pdf_path> [--pages N-M]

Outputs JSON to stdout (one JSON array of result objects).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pdfplumber
import anthropic

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Schema expected from the LLM ─────────────────────────────────────────────
RESULT_SCHEMA = {
    "event_name": "str — top-level event name (from cover/header)",
    "division": "str — division/group/class name, e.g. 'NorthEast Men', 'Open A Women', 'Vancouver'",
    "distance_m": "int|null — distance in meters, e.g. 500, 1000, 1500",
    "round": "str — 'Quarter-Final', 'Semi-Final', 'Final', 'A Final', 'B Final', etc.",
    "heat": "str|null — 'Heat 1 of 3', 'Heat 2', etc. null if not applicable",
    "rank": "int|null — finishing position (1-based), null if DNS/DNF/DQ",
    "bib": "str|null — bib/jersey number",
    "name": "str — skater full name",
    "time_text": "str|null — raw time string e.g. '1:23.456' or '02.22.677', null if no time",
    "status": "str|null — 'Q', 'ADV', 'DNS', 'DNF', 'DQ', 'PEN', etc. null if none",
}

SYSTEM_PROMPT = """\
You are extracting speed skating race results from PDF text.
Focus ONLY on individual race results (heats and finals) — not overall \
classification summaries or points tables.

Return a JSON array where each element represents ONE skater's result in ONE heat/race.
Use null for missing fields. Do not invent data.

Schema for each element:
""" + json.dumps(RESULT_SCHEMA, indent=2)

USER_PROMPT_TEMPLATE = """\
Extract all individual race results from the following PDF text.
Each heat/final should produce one row per skater.
Skip any "OVERALL CLASSIFICATION" or points summary pages.

Event name (from context): {event_name}

PDF TEXT:
{text}

Return ONLY a valid JSON array. No explanation, no markdown code fences."""


# ── Text extraction ───────────────────────────────────────────────────────────

def extract_pages(pdf_path: str | Path) -> list[tuple[int, str]]:
    """Return list of (page_number, text) for all non-empty pages."""
    result = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                result.append((i, text.strip()))
    return result


def guess_event_name(pages: list[tuple[int, str]]) -> str:
    """Best-effort event name from first non-empty page."""
    if pages:
        lines = [l.strip() for l in pages[0][1].split("\n") if l.strip()]
        return lines[0] if lines else "Unknown Event"
    return "Unknown Event"


def is_results_page(text: str) -> bool:
    """Return True if the page likely contains individual race results."""
    lower = text.lower()
    return any(kw in lower for kw in [
        "results", "quarter-final", "semi-final", "final", "heat ",
        "event #", "event results",
    ])


def is_summary_page(text: str) -> bool:
    """Return True if the page is an overall classification/summary page."""
    lower = text.lower()
    return any(kw in lower for kw in [
        "overall classification", "overall results", "classification a",
        "classification b", "points cdr", "skater # name affiliation points",
    ]) and "results" not in lower


# ── LLM call ─────────────────────────────────────────────────────────────────

def call_llm(client: anthropic.Anthropic, text_chunk: str, event_name: str) -> list[dict]:
    """Send a text chunk to Claude and return parsed results."""
    prompt = USER_PROMPT_TEMPLATE.format(event_name=event_name, text=text_chunk)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


# ── Main ─────────────────────────────────────────────────────────────────────

def extract_pdf(
    pdf_path: str | Path,
    pages_filter: tuple[int, int] | None = None,
    chunk_pages: int = 12,
) -> list[dict]:
    """
    Extract race results from a PDF using Claude.

    Args:
        pdf_path: Path to the PDF.
        pages_filter: Optional (start, end) 1-based page range to process.
        chunk_pages: Number of pages to send per LLM call.
    """
    client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY env var
    all_pages = extract_pages(pdf_path)
    event_name = guess_event_name(all_pages)
    logger.info("Event: %s | Total text pages: %d", event_name, len(all_pages))

    # Optional page filter
    if pages_filter:
        lo, hi = pages_filter
        all_pages = [(n, t) for n, t in all_pages if lo <= n <= hi]
        logger.info("Filtered to pages %d-%d: %d pages", lo, hi, len(all_pages))

    # Drop obvious summary-only pages
    result_pages = [(n, t) for n, t in all_pages if not is_summary_page(t)]
    # Include pages that look like results or haven't been classified
    result_pages = [(n, t) for n, t in result_pages if is_results_page(t)]
    logger.info("Result pages to process: %d", len(result_pages))

    all_results: list[dict] = []

    for i in range(0, len(result_pages), chunk_pages):
        chunk = result_pages[i : i + chunk_pages]
        chunk_text = "\n\n".join(f"[PAGE {n}]\n{t}" for n, t in chunk)
        page_nums = [n for n, _ in chunk]
        logger.info("  Calling LLM for pages %s...", page_nums)

        try:
            rows = call_llm(client, chunk_text, event_name)
            logger.info("    → %d results", len(rows))
            all_results.extend(rows)
        except Exception as e:
            logger.error("    LLM call failed: %s", e)

    return all_results


def main():
    parser = argparse.ArgumentParser(description="Extract race results from a PDF using Claude.")
    parser.add_argument("pdf", help="Path to the PDF file")
    parser.add_argument("--pages", help="Page range e.g. '4-25'", default=None)
    parser.add_argument("--chunk", type=int, default=12, help="Pages per LLM call (default 12)")
    parser.add_argument("--out", help="Output JSON file (default: stdout)")
    args = parser.parse_args()

    pages_filter = None
    if args.pages:
        lo, hi = args.pages.split("-")
        pages_filter = (int(lo), int(hi))

    results = extract_pdf(args.pdf, pages_filter=pages_filter, chunk_pages=args.chunk)

    output = json.dumps(results, indent=2)
    if args.out:
        Path(args.out).write_text(output)
        logger.info("Wrote %d results to %s", len(results), args.out)
    else:
        print(output)


if __name__ == "__main__":
    main()
