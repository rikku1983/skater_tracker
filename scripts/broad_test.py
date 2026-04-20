"""Broad parse test across all ST PDFs. Writes results to data/parse_test.json."""
import json
import sys
import logging
from pathlib import Path
from collections import defaultdict

# Suppress pdfplumber warnings
logging.disable(logging.WARNING)

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.parsers.base import detect_format, PdfFormat
from src.parsers import parse_pdf

pdfs = sorted(Path("data/raw_pdfs").rglob("*.pdf"))
print(f"Total PDFs: {len(pdfs)}", flush=True)

stats = defaultdict(int)
fmt_results = defaultdict(list)
errors_by_fmt = defaultdict(list)

for i, pdf in enumerate(pdfs):
    try:
        fmt = detect_format(pdf)
        stats[fmt.value] += 1
        if fmt in (PdfFormat.TEMPUS, PdfFormat.LEGACY):
            ev = parse_pdf(pdf)
            n = len(ev.results)
            fmt_results[fmt.value].append({"n": n, "name": pdf.name, "season": pdf.parent.name})
            if ev.parse_errors:
                errors_by_fmt[fmt.value].append({"name": pdf.name, "errors": ev.parse_errors[:3]})
        if (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(pdfs)}] processed...", flush=True)
    except Exception as e:
        stats["exception"] += 1
        errors_by_fmt["exception"].append({"name": pdf.name, "errors": [str(e)]})

out = {
    "stats": dict(stats),
    "fmt_results": dict(fmt_results),
    "errors_by_fmt": dict(errors_by_fmt),
}
outpath = Path("data/parse_test.json")
outpath.write_text(json.dumps(out, indent=2))
print(f"\nResults written to {outpath}", flush=True)

print("\n=== Format distribution ===")
for k, v in sorted(stats.items()):
    print(f"  {k}: {v}")

print("\n=== Result counts ===")
for fmt, items in fmt_results.items():
    counts = [x["n"] for x in items]
    zero = [x["name"] for x in items if x["n"] == 0]
    print(f"  {fmt}: total={sum(counts)} avg={sum(counts)//max(len(counts),1)} min={min(counts)} max={max(counts)} zero={len(zero)}")
    for name in zero:
        print(f"    ZERO: {name}")

print("\n=== Parse errors (sample) ===")
for fmt, errs in errors_by_fmt.items():
    print(f"  {fmt}: {len(errs)} PDFs with errors")
    for e in errs[:3]:
        print(f"    {e['name']}: {e['errors']}")
