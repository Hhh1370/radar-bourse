#!/usr/bin/env python3
"""Fetch public per-company Fundai ratio history with bounded parallel GET requests.

This stores secondary-source historical ratio observations. It does not claim the
ratios are audited statements and never fabricates values where the endpoint fails.
The previous valid dataset is kept unchanged if coverage collapses.
"""
from __future__ import annotations

import concurrent.futures
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from scripts.fundai_ratio_history import normalize_history_payload, normalize_symbol

ROOT = Path(__file__).resolve().parents[1]
RATIO_FILE = ROOT / "data" / "fundai_financial_ratios.json"
OUTPUT_FILE = ROOT / "data" / "fundai_financial_ratio_history.json"
BASE_URL = "https://fundai.ir/api/v1/fundamental/financial-ratio-history/"
MAX_WORKERS = 6
MAX_ATTEMPTS = 3
MAX_RESPONSE_BYTES = 500_000
TIMEOUT_SECONDS = 16
MIN_COVERAGE_RATIO = 0.65
MIN_SUCCESSFUL_HISTORIES = 350
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def fetch_one(stock: dict) -> tuple[dict | None, dict]:
    slug = str(stock.get("company_slug") or "").strip()
    symbol = str(stock.get("company") or "").strip()
    if not slug or not symbol:
        return None, {"symbol": symbol, "company_slug": slug, "error": "missing_symbol_or_slug"}
    query = urllib.parse.urlencode({"company_slug": slug})
    url = BASE_URL + "?" + query
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://fundai.ir/companies",
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                status = response.status
                content_type = response.headers.get("Content-Type", "")
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if status != 200:
                last_error = f"http_{status}"
                if status not in (429, 500, 502, 503, 504):
                    break
                time.sleep(min(2.0, 0.25 * attempt))
                continue
            if len(body) > MAX_RESPONSE_BYTES:
                last_error = "response_exceeded_size_limit"
                break
            if "json" not in content_type.lower():
                last_error = f"unexpected_content_type:{content_type[:70]}"
                break
            payload = json.loads(body.decode("utf-8-sig"))
            normalized = normalize_history_payload(
                payload,
                company_slug=slug,
                symbol=symbol,
                industry=str(stock.get("industry") or ""),
                tsetmc_id=str(stock.get("tsetmc_id") or ""),
                fetched_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            normalized["source_url"] = url
            normalized["http_status"] = status
            normalized["response_bytes"] = len(body)
            normalized["source"] = "Fundai public financial-ratio-history endpoint"
            return normalized, {"symbol": symbol, "company_slug": slug, "row_count": normalized["history_row_count"]}
        except urllib.error.HTTPError as exc:
            last_error = f"http_{exc.code}"
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = f"{type(exc).__name__}:{str(exc)[:150]}"
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            last_error = f"{type(exc).__name__}:{str(exc)[:150]}"
            break
        if attempt < MAX_ATTEMPTS:
            time.sleep(min(2.0, 0.3 * (2 ** (attempt - 1))))
    return None, {"symbol": symbol, "company_slug": slug, "error": last_error or "unknown_error"}


def load_latest_snapshot() -> dict:
    if not RATIO_FILE.is_file():
        raise FileNotFoundError("Fundai ratio snapshot missing; run fetch-fundai-ratios first.")
    payload = json.loads(RATIO_FILE.read_text(encoding="utf-8"))
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Fundai ratio snapshot contains no rows.")
    unique = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("company_slug") or "").strip()
        symbol = str(row.get("company") or "").strip()
        if slug and symbol:
            prior = unique.get(slug)
            if prior is None or str(row.get("report_date") or "") > str(prior.get("report_date") or ""):
                unique[slug] = row
    stocks = sorted(unique.values(), key=lambda x: normalize_symbol(x.get("company")))
    if len(stocks) < 500:
        raise ValueError(f"Only {len(stocks)} public Fundai company slugs found; refusing broad history refresh.")
    return payload, stocks


def main() -> None:
    started = time.monotonic()
    source_snapshot, stocks = load_latest_snapshot()
    print(f"FUNDAI_HISTORY_TARGET:{len(stocks)}")
    successful = []
    failed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_one, stock): stock for stock in stocks}
        total = 0
        for future in concurrent.futures.as_completed(futures):
            total += 1
            try:
                history, status = future.result()
            except Exception as exc:
                stock = futures[future]
                history = None
                status = {
                    "symbol": stock.get("company"),
                    "company_slug": stock.get("company_slug"),
                    "error": f"unexpected_worker_error:{type(exc).__name__}:{str(exc)[:120]}",
                }
            if history:
                successful.append(history)
            else:
                failed.append(status)
            if total % 50 == 0 or total == len(stocks):
                print(f"FUNDAI_HISTORY_PROGRESS:{total}/{len(stocks)} success={len(successful)} failed={len(failed)}")

    successful.sort(key=lambda x: normalize_symbol(x.get("symbol")))
    target = len(stocks)
    required = max(MIN_SUCCESSFUL_HISTORIES, math.ceil(target * MIN_COVERAGE_RATIO))
    if len(successful) < required:
        errors = Counter(str(item.get("error") or "unknown") for item in failed)
        raise RuntimeError(
            f"Only {len(successful)}/{target} histories fetched; required at least {required}. "
            f"Keeping previous valid file untouched. Main failures: {errors.most_common(6)}"
        )

    output = {
        "schema_version": 1,
        "source": "Fundai public financial-ratio-history endpoint",
        "source_url": BASE_URL,
        "method": (
            "Bounded read-only GET; same duration/month/day year-over-year comparisons only. "
            "Secondary ratio history is not an audited financial statement."
        ),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_snapshot_generated_at_utc": str(source_snapshot.get("generated_at_utc") or ""),
        "target_company_count": target,
        "successful_company_count": len(successful),
        "failed_company_count": len(failed),
        "coverage_pct": round(len(successful) / target * 100, 2),
        "total_history_rows": sum(int(row.get("history_row_count") or 0) for row in successful),
        "unique_duration_count": len({
            str(row.get("duration_code") or "")
            for history in successful
            for row in history.get("history_rows", [])
            if row.get("duration_code")
        }),
        "failure_categories": dict(Counter(str(item.get("error") or "unknown").split(":", 1)[0] for item in failed)),
        "failures_sample": failed[:40],
        "history_rows": successful,
        "quality_note": (
            "This is a public ratio time series, not an audited statement-level dataset. "
            "All comparisons require exact report duration and matching Jalali month/day; "
            "missing or mismatched periods are excluded from trend scoring."
        ),
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = OUTPUT_FILE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    reread = json.loads(temp.read_text(encoding="utf-8"))
    assert reread["successful_company_count"] >= required
    assert len(reread["history_rows"]) == reread["successful_company_count"]
    temp.replace(OUTPUT_FILE)
    print("FUNDAI_RATIO_HISTORY_SUMMARY")
    print(f"TARGET:{target}")
    print(f"SUCCESS:{len(successful)}")
    print(f"FAILED:{len(failed)}")
    print(f"COVERAGE_PCT:{output['coverage_pct']}")
    print(f"TOTAL_HISTORY_ROWS:{output['total_history_rows']}")
    print(f"ELAPSED_SECONDS:{round(time.monotonic() - started, 1)}")
    print(f"OUTPUT:{OUTPUT_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
