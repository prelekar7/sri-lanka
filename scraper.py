"""Download and import CBSL daily reports."""

from __future__ import annotations

import time
from datetime import date, timedelta

import requests

from database import insert_report, report_exists
from pdf_parser import parse_pdf, report_url_candidates


class ReportUnavailable(Exception):
    """The requested date has no published report."""


def fetch_report(report_date: date, timeout: int = 30) -> tuple[list[dict], str]:
    for url in report_url_candidates(report_date):
        response = requests.get(url, timeout=timeout, headers={"User-Agent": "SriLankaPriceDashboard/1.0"})
        if response.status_code == 404:
            continue
        response.raise_for_status()
        if not response.content.startswith(b"%PDF"):
            raise ValueError(f"CBSL returned a non-PDF response for {report_date.isoformat()}.")
        return parse_pdf(response.content, report_date=report_date), url
    raise ReportUnavailable(f"No report published for {report_date.isoformat()}.")


def import_report(report_date: date, force: bool = False) -> tuple[bool, int, str]:
    """Fetch and store one date. Returns (imported, rows, message)."""
    if report_exists(report_date) and not force:
        return False, 0, "Already present"
    try:
        records, url = fetch_report(report_date)
    except ReportUnavailable as exc:
        return False, 0, str(exc)
    insert_report(report_date, url, records)
    return True, len(records), "Imported"


def backfill(start_date: date, end_date: date, delay_seconds: float = 0.25):
    """Yield progress tuples while importing every date in an inclusive range."""
    current = start_date
    while current <= end_date:
        try:
            imported, count, message = import_report(current)
        except (requests.RequestException, ValueError) as exc:
            imported, count, message = False, 0, f"Error: {exc}"
        yield current, imported, count, message
        current += timedelta(days=1)
        time.sleep(delay_seconds)
