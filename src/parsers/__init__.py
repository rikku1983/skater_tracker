from .base import ParsedEvent, ParsedResult, PdfFormat, detect_format
from .tempus_parser import parse_tempus_pdf
from .legacy_parser import parse_legacy_pdf
from .all_races_parser import parse_all_races_pdf
from .tempus_results_parser import parse_tempus_results_pdf
from .speedskating_pro_parser import parse_speedskating_pro_pdf
from .tempus_races_parser import parse_tempus_races_pdf
from .classic_results_parser import parse_classic_results_pdf


def parse_pdf(pdf_path) -> ParsedEvent:
    """Auto-detect format and parse a PDF."""
    fmt = detect_format(pdf_path)
    if fmt == PdfFormat.ALL_RACES:
        return parse_all_races_pdf(pdf_path)
    elif fmt == PdfFormat.TEMPUS_RESULTS:
        return parse_tempus_results_pdf(pdf_path)
    elif fmt == PdfFormat.SPEEDSKATING_PRO:
        return parse_speedskating_pro_pdf(pdf_path)
    elif fmt == PdfFormat.TEMPUS_RACES:
        return parse_tempus_races_pdf(pdf_path)
    elif fmt == PdfFormat.TEMPUS:
        return parse_tempus_pdf(pdf_path)
    elif fmt == PdfFormat.CLASSIC_RESULTS:
        return parse_classic_results_pdf(pdf_path)
    elif fmt == PdfFormat.LEGACY:
        return parse_legacy_pdf(pdf_path)
    else:
        return ParsedEvent(pdf_path=str(pdf_path), format=fmt)
