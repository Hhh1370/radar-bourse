#!/usr/bin/env python3
"""Download Fundai's publicly accessible, read-only TTM financial-ratio table.

The endpoint is discovered from Fundai's public site and returns paginated
JSON without login. This script does not use cookies, tokens, paid API keys,
POST requests, or authentication bypasses.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "fundai_financial_ratios.json"
BASE_URL = "https://fundai.ir/api/v1/fundamental/financial-ratio-filters/"
MAX_PAGES = 80
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 12
PAGE_DELAY_SECONDS = 0.18
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
REQUIRED_FIELDS = ("company", "report_date", "price", "eps", "tsetmc_id")
METRIC_FIELDS = (
    "roe_ttm", "roa_ttm", "net_profit_margin_ttm",
    "pe_ttm", "ps_ttm", "pb", "debt_to_assets",
)


def fetch_json(url: str) -> tuple[dict, dict]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "fundai.ir":
        raise ValueError(f"Refusing to follow an unexpected pagination URL: {url}")
    if parsed.path.rstrip("/") != "/api/v1/fundamental/financial-ratio-filters":
        raise ValueError(f"Refusing to follow an unexpected API path: {parsed.path}")

    with tempfile.NamedTemporaryFile(prefix="fundai-ratios-", suffix=".json") as target:
        command = [
            "curl", "--location", "--http1.1",
            "--connect-timeout", "5", "--max-time", str(TIMEOUT_SECONDS),
            "--max-filesize", str(MAX_RESPONSE_BYTES), "--retry", "1",
            "--retry-delay", "1", "--retry-all-errors",
            "--user-agent", USER_AGENT,
            "--header", "Accept: application/json",
            "--header", "Referer: https://fundai.ir/filters",
            "--silent", "--show-error",
            "--output", target.name,
            "--write-out", "%{http_code}|%{content_type}|%{url_effective}",
            url,
        ]
        try:
            proc = subprocess.run(
                command, capture_output=True, text=True,
                timeout=TIMEOUT_SECONDS * 2 + 4, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Timeout fetching Fundai page: {url}") from exc

        fields = (proc.stdout or "").strip().split("|", 2)
        raw_status = fields[0] if fields and fields[0] else "ERROR"
        try:
            status = int(raw_status)
        except ValueError:
            status = raw_status
        content_type = fields[1] if len(fields) > 1 else ""
        effective_url = fields[2] if len(fields) > 2 else url

        target.flush()
        size = Path(target.name).stat().st_size
        with open(target.name, "rb") as handle:
            body = handle.read(MAX_RESPONSE_BYTES + 1)

        error = (proc.stderr or "").strip()
        if proc.returncode == 63 or size > MAX_RESPONSE_BYTES:
            error = f"response exceeded {MAX_RESPONSE_BYTES}-byte safety limit"
        elif proc.returncode and not error:
            error = f"curl exited with code {proc.returncode}"

        if proc.returncode != 0 or status != 200 or len(body) == 0 or len(body) > MAX_RESPONSE_BYTES:
            excerpt = body[:500].decode("utf-8", "replace")
            raise RuntimeError(
                f"Fundai fetch failed: status={status}, curl={proc.returncode}, "
                f"bytes={len(body)}, error={error[:250]}, excerpt={excerpt[:250]}"
            )
        if "json" not in content_type.lower():
            raise RuntimeError(f"Expected JSON from Fundai; got Content-Type={content_type!r}")

        try:
            payload = json.loads(body.decode("utf-8-sig"))
        except Exception as exc:
            raise RuntimeError(f"Invalid JSON from Fundai: {url}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise RuntimeError(
                f"Unexpected Fundai response shape at {url}; "
                f"keys={list(payload)[:30] if isinstance(payload, dict) else type(payload).__name__}"
            )
        return payload, {
            "url": url,
            "effective_url": effective_url,
            "http_status": status,
            "content_type": content_type,
            "body_bytes": len(body),
            "rows": len(payload["results"]),
            "elapsed_seconds": None,
        }


def norm(value) -> str:
    text = str(value or "").strip()
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    text = text.translate(table).replace("ي", "ی").replace("ك", "ک").replace("\u200c", "")
    return re.sub(r"[\s\-_.]+", "", text).casefold()


def valid_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def main() -> None:
    start = time.monotonic()
    pages = []
    rows = []
    page_url = BASE_URL
    reported_count = None
    seen_urls = set()
    page_number = 0

    while page_url:
        page_number += 1
        if page_number > MAX_PAGES:
            raise RuntimeError(f"Pagination exceeded safe cap ({MAX_PAGES} pages); output not replaced.")
        if page_url in seen_urls:
            raise RuntimeError(f"Fundai pagination loop detected at {page_url}")
        seen_urls.add(page_url)

        payload, meta = fetch_json(page_url)
        if reported_count is None:
            count = payload.get("count")
            if not isinstance(count, int) or count < 1:
                raise RuntimeError(f"Fundai did not return a valid count: {count!r}")
            reported_count = count

        page_rows = payload.get("results", [])
        if not page_rows:
            raise RuntimeError(f"Empty page before pagination ended: {page_url}")
        if any(not isinstance(row, dict) for row in page_rows):
            raise RuntimeError(f"Non-object financial-ratio row returned on page {page_number}")

        rows.extend(page_rows)
        pages.append(meta)
        next_url = payload.get("next")
        if next_url:
            next_parsed = urlparse(next_url)
            if next_parsed.scheme != "https" or next_parsed.hostname != "fundai.ir":
                raise RuntimeError(f"Rejected unexpected next page URL: {next_url}")
            page_url = next_url
        else:
            page_url = None

        print(
            f"FUNDAI_PAGE:{page_number} HTTP:{meta['http_status']} "
            f"ROWS:{meta['rows']} TOTAL_DOWNLOADED:{len(rows)} "
            f"NEXT:{'yes' if page_url else 'no'}"
        )
        if page_url:
            time.sleep(PAGE_DELAY_SECONDS)

    tickers = [norm(row.get("company")) for row in rows if norm(row.get("company"))]
    unique_tickers = set(tickers)
    isin_ids = {str(row.get("tsetmc_id") or "").strip() for row in rows if str(row.get("tsetmc_id") or "").strip()}
    report_dates = [str(row.get("report_date") or "").strip() for row in rows if str(row.get("report_date") or "").strip()]
    fields_present = {
        field: sum(1 for row in rows if row.get(field) not in (None, ""))
        for field in ("company", "industry", "report_date", "price", "eps", "pe_ttm",
                      "ps_ttm", "market_value", "operation_income", "net_profit_ttm",
                      "net_profit_margin_ttm", "roe_ttm", "roa_ttm", "debt_to_assets", "tsetmc_id")
    }
    ratios_valid = {
        field: sum(1 for row in rows if valid_number(row.get(field)))
        for field in METRIC_FIELDS
    }

    # Keep the last row per ticker if the provider ever includes duplicate
    # ticker aliases, but retain the raw fetched rows in the source file.
    duplicate_tickers = len(tickers) - len(unique_tickers)
    quality_notes = []
    if len(rows) != reported_count:
        quality_notes.append(
            f"Provider reports {reported_count} rows but pagination returned {len(rows)}."
        )
    if len(unique_tickers) < 500:
        raise RuntimeError(
            f"Only {len(unique_tickers)} distinct tickers fetched; expected broad coverage. "
            "Keeping any previous valid output unchanged."
        )
    if duplicate_tickers:
        quality_notes.append(f"{duplicate_tickers} duplicate normalized tickers; review before joining.")
    if sum(1 for row in rows if row.get("report_date")) < 400:
        quality_notes.append("Report-date coverage is low; do not treat missing dates as current.")

    output = {
        "schema_version": 1,
        "source": "Fundai public financial-ratio table",
        "source_url": BASE_URL,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider_reported_count": reported_count,
        "downloaded_row_count": len(rows),
        "unique_normalized_ticker_count": len(unique_tickers),
        "unique_tsetmc_id_count": len(isin_ids),
        "duplicate_normalized_ticker_count": duplicate_tickers,
        "page_count": len(pages),
        "fetch_elapsed_seconds": round(time.monotonic() - start, 2),
        "field_coverage": fields_present,
        "numeric_metric_coverage": ratios_valid,
        "page_log": pages,
        "quality_notes": quality_notes,
        "methodology_note": (
            "Latest displayed TTM/financial-ratio snapshot only. Not a full audited "
            "financial-statement history and not, by itself, a credit rating. Preserve "
            "source values and dates; validate issuer, industry, units and outliers before scoring."
        ),
        "rows": rows,
    }

    # Write to a temporary file then atomically replace. A partial or low-coverage
    # run never overwrites the last valid copy.
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp_out = OUT.with_suffix(".json.tmp")
    temp_out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reread = json.loads(temp_out.read_text(encoding="utf-8"))
    assert len(reread["rows"]) == len(rows)
    assert reread["unique_normalized_ticker_count"] >= 500
    temp_out.replace(OUT)

    print("FUNDAI_FINANCIAL_RATIOS_SUMMARY")
    print(f"PROVIDER_REPORTED_COUNT:{reported_count}")
    print(f"DOWNLOADED_ROWS:{len(rows)}")
    print(f"UNIQUE_TICKERS:{len(unique_tickers)}")
    print(f"UNIQUE_TSETMC_IDS:{len(isin_ids)}")
    print(f"PAGES:{len(pages)}")
    print(f"METRIC_COVERAGE:{json.dumps(ratios_valid, ensure_ascii=False)}")
    print(f"QUALITY_NOTES:{json.dumps(quality_notes, ensure_ascii=False)}")
    print(f"OUTPUT:{OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
