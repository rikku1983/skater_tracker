"""
Temporary script: extract TIME CLASSIFICATION from pages 12-23 of the January Thaw 2024 PDF.

Pages are text-based (pdfplumber reads them directly). Format:
  Division name
  NNNN Meters
  Skater # Name Best Time
  rank  bib  name...  M:SS.sss

Output: data/january_thaw_2024_time_classification.csv
"""
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import pdfplumber

PDF_PATH = Path("data/pdfs/2023-2024/2024_January_Thaw_Protocal_Report.pdf")
OUT_PATH = Path("data/january_thaw_2024_time_classification.csv")

PAGES = range(12, 24)  # pages 12-23 (1-indexed)

TIME_RE   = re.compile(r'^\d+:\d{2}\.\d+$')
DIST_RE   = re.compile(r'^(\d+)\s+Meters?$', re.I)
SKIP_LINES = {
    'TIME CLASSIFICATION',
    'Skater # Name Best Time',
}


def _parse_time(text: str) -> float | None:
    m = re.match(r'^(\d+):(\d{2})\.(\d+)$', text)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / (10 ** len(m.group(3)))


def main():
    rows_out: list[dict] = []
    cur_division: str | None = None
    cur_distance_m: int | None = None

    with pdfplumber.open(str(PDF_PATH)) as pdf:
        for pg_idx in [p - 1 for p in PAGES]:
            page = pdf.pages[pg_idx]
            page_num = pg_idx + 1
            text = page.extract_text() or ''

            for line in text.split('\n'):
                line = line.strip()
                if not line:
                    continue

                # Skip known header lines and the event title
                if line in SKIP_LINES:
                    continue
                if 'January Thaw' in line or 'Championship' in line:
                    continue

                # Distance line: "1500 Meters"
                dm = DIST_RE.match(line)
                if dm:
                    cur_distance_m = int(dm.group(1))
                    continue

                # Data row: first token is rank (digit), last token is time
                tokens = line.split()
                if not tokens:
                    continue

                if tokens[0].isdigit() and len(tokens) >= 3:
                    # Check if last token is a time
                    last = tokens[-1]
                    if TIME_RE.match(last):
                        rank = int(tokens[0])
                        bib  = tokens[1]
                        # Name is everything between bib and time, strip trailing *
                        name = ' '.join(tokens[2:-1]).replace('*', '').strip()
                        rows_out.append({
                            'division':       cur_division,
                            'distance_m':     cur_distance_m,
                            'rank':           rank,
                            'bib':            bib,
                            'name':           name,
                            'best_time_text': last,
                            'best_time_sec':  _parse_time(last),
                            'page':           page_num,
                        })
                        continue

                # Otherwise treat as a division name (if it doesn't look like a data row)
                # Skip lines that start with a digit but aren't data rows (continuation lines)
                if not tokens[0].isdigit():
                    cur_division = line
                    cur_distance_m = None  # reset distance on new division

    # Write CSV
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ['division', 'distance_m', 'rank', 'bib', 'name',
                  'best_time_text', 'best_time_sec', 'page']
    with open(OUT_PATH, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"Wrote {len(rows_out)} rows to {OUT_PATH}")

    # Summary
    from collections import Counter
    by_div = Counter((r['division'], r['distance_m']) for r in rows_out)
    print("\nDivision / distance breakdown:")
    for (div, dist), n in sorted(by_div.items()):
        print(f"  {div!r:30s} {str(dist)+'m':8s} {n} skaters")


if __name__ == '__main__':
    main()
