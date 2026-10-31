"""Extract daily market prices from CBSL price report PDFs."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pdfplumber

CBSL_PDF_BASE = (
    "https://www.cbsl.gov.lk/sites/default/files/cbslweb_documents/"
    "statistics/pricerpt/price_report_"
)

CATEGORY_NAMES = {
    "VEGETABLES": "Vegetables",
    "OTHER": "Other",
    "FRUITS": "Fruits",
    "RICE": "Rice",
    "FISH": "Fish",
    "ESSENTIALS": "Essentials",
}

# The table has five market pairs; fish and rice sections override some
# market labels while retaining the same column positions.
STANDARD_COLUMNS = [
    ("Pettah", "wholesale"),
    ("Dambulla", "wholesale"),
    ("Pettah", "retail"),
    ("Dambulla", "retail"),
    ("Narahenpita", "retail"),
]

LEGACY_COLUMNS = {
    "Vegetables": [
        ("Pettah", "wholesale"), ("Pettah", "retail"),
        ("Dambulla", "wholesale"), ("Dambulla", "retail"),
    ],
    "Other": [
        ("Pettah", "wholesale"), ("Pettah", "retail"),
        ("Dambulla", "wholesale"), ("Dambulla", "retail"),
    ],
    "Rice": [
        ("Pettah", "wholesale"), ("Pettah", "retail"),
        ("Marandagahamula", "wholesale"),
    ],
    "Fish": [
        ("Peliyagoda", "wholesale"), ("Peliyagoda", "retail"),
        ("Negombo", "wholesale"), ("Negombo", "retail"),
    ],
    "Fruits": [("Pettah", "wholesale"), ("Pettah", "retail")],
}

LEGACY_PRICE_RE = re.compile(
    r"(?<![\w.])(?:\d\s*,?\s*)?\d{1,3}(?:\s*,\s*\d{3})*\.\d{2}|\bn\.a\.(?!\w)",
    re.I,
)
LEGACY_ROW_RE = re.compile(r"^(.*?)\s+Rs\.?\s*/\s*(kg|Nut|Each|Ltr)\s+(.*)$", re.I)


def _parse_price(token: str) -> float | None:
    value = re.sub(r"\s+", "", token).replace(",", "")
    if value.lower().startswith("n.a"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_legacy_pages(pages, report_date: date) -> list[dict]:
    """Parse the older multi-page table with weekly-average/date pairs."""
    categories = {re.sub(r"\s+", "", key.upper()): value for key, value in CATEGORY_NAMES.items()}
    records: list[dict] = []
    page_fallback = ["Vegetables", "Rice", "Fish", "Fruits", "Other"]

    for page_index, page in enumerate(pages):
        category = page_fallback[page_index] if page_index < len(page_fallback) else None
        for raw_line in (page.extract_text() or "").splitlines():
            line = raw_line.strip()
            normalized = re.sub(r"\s+", "", line).upper()
            if normalized in categories:
                category = categories[normalized]
                continue
            if category not in LEGACY_COLUMNS:
                continue
            match = LEGACY_ROW_RE.match(line)
            if not match:
                continue
            item, unit, values_text = match.groups()
            item = re.sub(r"\s+", " ", item).strip()
            if not item or item.lower() in {"item", "previous", "average"}:
                continue
            tokens = LEGACY_PRICE_RE.findall(values_text)
            columns = LEGACY_COLUMNS[category]
            tokens = tokens[: 2 * len(columns)]
            tokens += ["n.a."] * (2 * len(columns) - len(tokens))

            for index, (market, price_type) in enumerate(columns):
                reference_price = _parse_price(tokens[index * 2])
                current_price = _parse_price(tokens[index * 2 + 1])
                if reference_price is None and current_price is None:
                    continue
                records.append(
                    {
                        "category": category,
                        "item_name": item,
                        "unit": f"Rs./{unit}",
                        "market": market,
                        "price_type": price_type,
                        "yesterday_price": reference_price,
                        "today_price": current_price,
                        "comparison_label": "Previous 5 Days Average",
                    }
                )
    return records


def _report_date_from_text(text: str) -> date | None:
    match = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if not match:
        return None
    from datetime import datetime

    try:
        return datetime.strptime(" ".join(match.groups()), "%d %B %Y").date()
    except ValueError:
        return None


def parse_pdf(source: str | Path | bytes, report_date: date | None = None) -> list[dict]:
    """Return normalized price observations from page 2 of a CBSL PDF."""
    import io

    handle = io.BytesIO(source) if isinstance(source, bytes) else source
    records: list[dict] = []
    with pdfplumber.open(handle) as pdf:
        if not pdf.pages:
            return records
        first_text = pdf.pages[0].extract_text() or ""
        second_text = pdf.pages[1].extract_text() if len(pdf.pages) > 1 else ""
        report_date = report_date or _report_date_from_text(first_text + "\n" + (second_text or ""))
        if report_date is None:
            raise ValueError("Could not determine report date from the PDF; provide report_date.")

        page = pdf.pages[1] if len(pdf.pages) > 1 else None
        words = page.extract_words() if page else []
        # The cells often omit unavailable prices entirely. Reading the PDF's
        # x-positions preserves empty cells and prevents every later value from
        # shifting into the wrong market.
        rows: list[list[dict]] = []
        for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
            if not rows or abs(rows[-1][0]["top"] - word["top"]) > 1.5:
                rows.append([word])
            else:
                rows[-1].append(word)

        date_columns = [
            word["x0"]
            for row in rows
            for word in row
            if word["text"].lower() in {"yesterday", "today"} and word["top"] < 90
        ][:10]
        if len(date_columns) != 10:
            legacy_records = _parse_legacy_pages(pdf.pages, report_date)
            if legacy_records:
                return legacy_records
            raise ValueError("Could not locate modern or legacy price columns in the PDF table.")

        category = "Other"
        market_overrides: dict[int, str] = {}
        for row in rows:
            row_text = " ".join(w["text"] for w in sorted(row, key=lambda w: w["x0"]))
            normalized_header = re.sub(r"\s+", "", row_text).upper()
            if normalized_header in CATEGORY_NAMES:
                category = CATEGORY_NAMES[normalized_header]
                market_overrides = {}
                continue

            # Some sections replace the usual market names; labels are centered
            # over the corresponding market pair in the PDF.
            is_market_header = not any(w["x0"] < 105 for w in row)
            if category in {"Rice", "Fish"} and is_market_header:
                for word in row:
                    if word["x0"] >= 140 and word["text"].lower() not in {"yesterday", "today"}:
                        pair_index = min(range(5), key=lambda i: abs(word["x0"] - (date_columns[i * 2] + date_columns[i * 2 + 1]) / 2))
                        if word["text"].lower() not in {"n.a.", "rs./kg"}:
                            market_overrides[pair_index] = word["text"]

            item_words = [w for w in row if w["x0"] < 105]
            unit_words = [w for w in row if 105 <= w["x0"] < 150]
            if not item_words or not unit_words:
                continue
            item = " ".join(w["text"] for w in sorted(item_words, key=lambda w: w["x0"]))
            unit = "".join(w["text"] for w in sorted(unit_words, key=lambda w: w["x0"]))
            if not unit.lower().startswith("rs./"):
                continue

            values: list[float | None] = [None] * 10
            raw_values: list[list[str]] = [[] for _ in range(10)]
            for word in row:
                if word["x0"] < 150:
                    continue
                column = min(range(10), key=lambda i: abs(word["x0"] - date_columns[i]))
                if re.fullmatch(r"(?:n\.a\.|[\d,]+(?:\.\d+)?)", word["text"], re.I):
                    raw_values[column].append(word["text"])
            for column, parts in enumerate(raw_values):
                if parts:
                    values[column] = _parse_price(" ".join(parts))

            for index, (default_market, price_type) in enumerate(STANDARD_COLUMNS):
                yesterday, today = values[index * 2], values[index * 2 + 1]
                if yesterday is None and today is None:
                    continue
                records.append(
                    {
                        "category": category,
                        "item_name": item,
                        "unit": unit,
                        "market": market_overrides.get(index, default_market),
                        "price_type": price_type,
                        "yesterday_price": yesterday,
                        "today_price": today,
                        "comparison_label": "Yesterday",
                    }
                )
    if not records:
        raise ValueError("No price rows were extracted from the report table.")
    return records


def report_url(report_date: date) -> str:
    return f"{CBSL_PDF_BASE}{report_date.strftime('%Y%m%d')}_e.pdf"


def report_url_candidates(report_date: date) -> list[str]:
    """CBSL used both filename suffixes during its archive history."""
    compact_date = report_date.strftime("%Y%m%d")
    return [f"{CBSL_PDF_BASE}{compact_date}_e.pdf", f"{CBSL_PDF_BASE}{compact_date}e.pdf"]
