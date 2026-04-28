"""
LLM-based PDF parser using Claude.

Handles PDFs that rule-based parsers cannot parse:
- Non-standard result layouts (web exports, spreadsheet PDFs, custom formats)
- Image-based PDFs (scanned) via Claude vision

Sends each page's text (or image) to Claude and asks it to extract
structured race results as JSON, then converts to ParsedResult objects.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Optional

import anthropic
import pdfplumber

from .base import ParsedEvent, ParsedResult, PdfFormat, _extract_distance_m
from ..utils.times import parse_time

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"   # fastest + cheapest
MAX_PAGES_PER_CALL = 1               # 1 page per call — keeps responses small and predictable
MAX_TOKENS = 4096                    # output token cap per call
API_CALL_DELAY = 35                  # seconds between calls (stays under 8k tokens/min limit)

_SYSTEM = """You are a data extraction assistant for US short track speed skating results.
Your job is to extract race results from PDF pages and return them as structured JSON.
Be precise: only extract data that is explicitly present on the page."""

_PROMPT_TEXT = """\
These are pages from a US short track speed skating results PDF.
Extract all race results you find.

Return a JSON object with this exact structure:
{{
  "event_name": "string or null",
  "event_date": "YYYY-MM-DD or null",
  "venue": "string or null",
  "sections": [
    {{
      "division": "string (e.g. 'Heartland Men', 'NEST Ladies', 'Junior B')",
      "distance_m": integer (meters; if only laps given, multiply by 111 — e.g. 7 laps = 777m),
      "round": "string (e.g. 'Final A', 'Heats', 'Distance Classification') or null",
      "results": [
        {{
          "rank": integer or null,
          "bib": "string or null",
          "name": "First Last (human name only, no club or numbers)",
          "club": "string or null",
          "time": "M:SS.mmm or SS.mmm or null",
          "status": "DNS/DNF/DQ/PEN or null",
          "points": number or null
        }}
      ]
    }}
  ]
}}

Rules:
- distance_m must be an integer (500, 777, 1000, 1500, 333, 222, 111, etc.)
- If a page is a cover, schedule, skater registration list, or contains no race results, return {{"sections": []}}
- Do NOT invent data. If something is unclear, omit it (use null)
- Names: "First Last" format. Strip asterisks, gender codes, category codes
- One section per unique (division + distance + round) combination
- If multiple distances appear for the same division, create separate sections
- Skip relay total times; only include individual skater rows
- IMPORTANT: Results at the TOP of a page often belong to the race section that
  started on the PREVIOUS page. Use the "Previous page context" below to
  determine the correct division and distance for those results.

{context_block}Page content:
{text}
"""

_PROMPT_IMAGE = """\
This is a page from a US short track speed skating results PDF (scanned image).
Extract all race results visible on this page.

Return a JSON object with this exact structure:
{{
  "event_name": "string or null",
  "event_date": "YYYY-MM-DD or null",
  "venue": "string or null",
  "sections": [
    {{
      "division": "string",
      "distance_m": integer,
      "round": "string or null",
      "results": [
        {{
          "rank": integer or null,
          "bib": "string or null",
          "name": "First Last",
          "club": "string or null",
          "time": "M:SS.mmm or SS.mmm or null",
          "status": "DNS/DNF/DQ/PEN or null",
          "points": number or null
        }}
      ]
    }}
  ]
}}

Rules:
- distance_m must be an integer (convert laps: 7 laps × 111m = 777m)
- Names are human names only — no club codes, no numbers
- If this is a skater registration/entry list (no times shown), a schedule, or a cover page, return {{"sections": []}}
- Only extract rows that have actual race times or status codes (DNS/DNF/DQ)
- IMPORTANT: Results at the top of this page may belong to a race section that
  started on the previous page. {context_note}
"""


def _page_to_base64(page) -> str:
    """Render a pdfplumber page to a base64-encoded PNG."""
    img = page.to_image(resolution=150)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode()


def _find_balanced(text: str, start: int) -> int:
    """Return the index of the closing brace/bracket matching the one at start, or -1."""
    open_ch = text[start]
    close_ch = '}' if open_ch == '{' else ']'
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == '\\' and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
    return -1


def _extract_json(raw: str) -> dict:
    """
    Extract structured data from Claude's response.
    Handles: complete JSON, JSON in code fences, and truncated JSON
    (extracts all complete section objects even if outer JSON is cut off).
    """
    raw = raw.strip()
    # Strip markdown fences
    raw = re.sub(r'^```(?:json)?\s*\n?', '', raw)
    raw = re.sub(r'\n?```\s*$', '', raw)

    # Try direct parse first
    try:
        result = json.loads(raw)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # --- Partial extraction for truncated responses ---
    out: dict = {"sections": []}

    # Extract simple top-level string fields with regex (robust to truncation)
    for key in ("event_name", "event_date", "venue"):
        m = re.search(rf'"{key}"\s*:\s*("(?:[^"\\]|\\.)*"|null)', raw)
        if m:
            try:
                out[key] = json.loads(m.group(1))
            except Exception:
                pass

    # Find and extract all complete section objects
    sections = []
    pos = 0
    while True:
        # Look for a section-like object: starts with { and contains "division"
        idx = raw.find('"division"', pos)
        if idx == -1:
            break
        # Find the opening { of this section object
        obj_start = raw.rfind('{', pos, idx)
        if obj_start == -1:
            pos = idx + 1
            continue
        obj_end = _find_balanced(raw, obj_start)
        if obj_end == -1:
            break  # truncated — stop here
        try:
            obj = json.loads(raw[obj_start:obj_end + 1])
            if isinstance(obj, dict) and "division" in obj:
                sections.append(obj)
        except json.JSONDecodeError:
            pass
        pos = obj_end + 1

    out["sections"] = sections
    if not sections and '"division"' not in raw:
        logger.debug("No sections found in response (may be empty page)")
    return out


def _call_claude_text(client: anthropic.Anthropic, pages_text: list[str],
                      prev_page_text: str = "") -> dict:
    combined = "\n\n--- PAGE BREAK ---\n\n".join(pages_text)
    if prev_page_text:
        # Include last ~60 lines of previous page so the LLM can see the active
        # section header even when it started on the prior page.
        tail = "\n".join(prev_page_text.splitlines()[-60:])
        context_block = (
            "Previous page context (use to identify the active race section "
            "at the top of the current page):\n"
            "--- PREVIOUS PAGE (tail) ---\n"
            f"{tail}\n"
            "--- END PREVIOUS PAGE ---\n\n"
        )
    else:
        context_block = ""
    prompt = _PROMPT_TEXT.format(context_block=context_block, text=combined[:8000])
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_json(msg.content[0].text)


def _call_claude_vision(client: anthropic.Anthropic, page_b64: str, page_num: int,
                        prev_page_text: str = "") -> dict:
    if prev_page_text:
        tail = "\n".join(prev_page_text.splitlines()[-40:])
        context_note = (
            f"The previous page ended with:\n"
            f"--- PREVIOUS PAGE (tail) ---\n{tail}\n--- END ---\n"
            "Use it to assign the correct division/distance to results at the top of this image."
        )
    else:
        context_note = "No previous page context available."
    prompt = _PROMPT_IMAGE.format(context_note=context_note)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=_SYSTEM,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": page_b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return _extract_json(msg.content[0].text)


def _sections_to_results(sections: list[dict], page_num: int) -> list[ParsedResult]:
    out: list[ParsedResult] = []
    for sec in sections:
        division = sec.get("division") or ""
        distance_m = sec.get("distance_m")
        round_str = sec.get("round")
        if distance_m is not None:
            try:
                distance_m = int(distance_m)
            except (TypeError, ValueError):
                distance_m = None

        for row in sec.get("results", []):
            name = (row.get("name") or "").strip()
            if not name:
                continue
            # Skip roster/registration entries (no time and no status)
            if not row.get("time") and not row.get("status"):
                continue
            time_text = row.get("time") or None
            time_s = parse_time(time_text) if time_text else None
            try:
                rank = int(row["rank"]) if row.get("rank") is not None else None
            except (TypeError, ValueError):
                rank = None
            try:
                points = float(row["points"]) if row.get("points") is not None else None
            except (TypeError, ValueError):
                points = None

            out.append(ParsedResult(
                division=division,
                distance_m=distance_m,
                distance_raw=f"{distance_m}m" if distance_m else "",
                rank=rank,
                bib=str(row["bib"]) if row.get("bib") is not None else None,
                name=name,
                affiliation=row.get("club") or "",
                seed_heat=None,
                heat_assignment=None,
                round=round_str,
                time_text=time_text,
                time_seconds=time_s,
                points=points,
                status=row.get("status") or None,
                page_number=page_num,
            ))
    return out


def parse_pdf_with_llm(
    pdf_path: str | Path,
    client: Optional[anthropic.Anthropic] = None,
) -> ParsedEvent:
    """
    Parse a PDF using Claude as the extraction engine.
    Falls back to vision API for pages with no extractable text.
    """
    pdf_path = Path(pdf_path)
    if client is None:
        client = anthropic.Anthropic()

    parsed = ParsedEvent(pdf_path=str(pdf_path), format=PdfFormat.UNKNOWN)

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            total_pages = len(pdf.pages)

            # Gather text per page; flag image pages
            page_texts: list[tuple[int, str, bool]] = []  # (page_num, text, is_image)
            for i, page in enumerate(pdf.pages, 1):
                text = (page.extract_text() or "").strip()
                page_texts.append((i, text, len(text) < 50))

            # Extract event metadata from first text page
            for _, text, is_img in page_texts[:5]:
                if not is_img and text:
                    first_line = text.split("\n")[0].strip()
                    if first_line:
                        parsed.event_name = first_line
                    break

            # Process in batches, carrying the previous page as context so the
            # LLM can resolve section headers that started on the prior page.
            batch_texts: list[str] = []
            batch_start_page = 1
            prev_page_text: str = ""   # tail of last successfully processed page

            call_count = 0

            def flush_text_batch(texts: list[str], start: int, prev: str):
                nonlocal call_count
                if not texts:
                    return
                if call_count > 0:
                    time.sleep(API_CALL_DELAY)
                call_count += 1
                try:
                    data = _call_claude_text(client, texts, prev_page_text=prev)
                    if not parsed.event_name and data.get("event_name"):
                        parsed.event_name = data["event_name"]
                    if not parsed.event_date_raw and data.get("event_date"):
                        parsed.event_date_raw = data["event_date"]
                    if not parsed.venue and data.get("venue"):
                        parsed.venue = data["venue"]
                    results = _sections_to_results(data.get("sections", []), start)
                    parsed.results.extend(results)
                    logger.debug("Pages %d+: extracted %d results", start, len(results))
                except Exception as e:
                    err = f"LLM text parse failed (pages from {start}): {e}"
                    parsed.parse_errors.append(err)
                    logger.warning(err)

            for page_num, text, is_image in page_texts:
                if is_image:
                    flush_text_batch(batch_texts, batch_start_page, prev_page_text)
                    if batch_texts:
                        prev_page_text = batch_texts[-1]
                    batch_texts = []
                    batch_start_page = page_num + 1

                    if call_count > 0:
                        time.sleep(API_CALL_DELAY)
                    call_count += 1
                    try:
                        page = pdf.pages[page_num - 1]
                        b64 = _page_to_base64(page)
                        data = _call_claude_vision(client, b64, page_num,
                                                   prev_page_text=prev_page_text)
                        if not parsed.event_name and data.get("event_name"):
                            parsed.event_name = data["event_name"]
                        results = _sections_to_results(data.get("sections", []), page_num)
                        parsed.results.extend(results)
                        logger.debug("Page %d (vision): extracted %d results", page_num, len(results))
                    except Exception as e:
                        err = f"LLM vision failed (page {page_num}): {e}"
                        parsed.parse_errors.append(err)
                        logger.warning(err)
                    prev_page_text = text  # image page text may be empty but update anyway
                else:
                    if len(batch_texts) >= MAX_PAGES_PER_CALL:
                        flush_text_batch(batch_texts, batch_start_page, prev_page_text)
                        prev_page_text = batch_texts[-1]
                        batch_texts = []
                        batch_start_page = page_num
                    batch_texts.append(text)

            flush_text_batch(batch_texts, batch_start_page, prev_page_text)

    except Exception as e:
        parsed.parse_errors.append(f"PDF error: {e}")
        logger.error("Failed to parse %s: %s", pdf_path.name, e)

    parsed.is_parseable = len(parsed.results) > 0
    logger.info(
        "%s → %d results via LLM (errors: %d)",
        pdf_path.name, len(parsed.results), len(parsed.parse_errors),
    )
    return parsed
