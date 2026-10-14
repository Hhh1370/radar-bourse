#!/usr/bin/env python3
"""Probe Fundai's publicly visible per-company financial-ratio history route.

This is a bounded read-only GET probe against the route used by Fundai's public
frontend. It does not use credentials, POST requests, browser cookies, or auth
bypass. A successful HTTP response is only schema evidence, not audited data.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RATIO_FILE = ROOT / "data" / "fundai_financial_ratios.json"
OUTPUT_FILE = ROOT / "data" / "fundai_ratio_history_probe.json"
BASE_URL = "https://fundai.ir/api/v1/fundamental/financial-ratio-history/"
MAX_SYMBOLS = 5
MAX_BODY_BYTES = 1_000_000
TIMEOUT_SECONDS = 12
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def inspect_payload(value):
    summary = {"top_type": type(value).__name__, "top_keys": [], "arrays": []}

    def walk(node, path="$", depth=0):
        if depth > 5:
            return
        if isinstance(node, dict):
            if path == "$":
                summary["top_keys"] = list(node.keys())[:40]
            for key, child in list(node.items())[:100]:
                walk(child, f"{path}.{key}", depth + 1)
        elif isinstance(node, list):
            first = node[0] if node else None
            summary["arrays"].append({
                "path": path,
                "count": len(node),
                "first_item_type": type(first).__name__ if node else None,
                "first_item_keys": list(first.keys())[:35] if isinstance(first, dict) else [],
                "first_item_sample": first if isinstance(first, dict) else None,
            })
            # Metadata only: inspect the first record, not the entire history.
            if node and isinstance(first, dict):
                for key, child in list(first.items())[:35]:
                    if isinstance(child, (dict, list)):
                        walk(child, f"{path}[0].{key}", depth + 1)

    walk(value)
    summary["arrays"] = summary["arrays"][:20]
    return summary


def probe(slug):
    url = BASE_URL + "?" + urllib.parse.urlencode({"company_slug": slug})
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Referer": "https://fundai.ir/companies",
    })
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_BODY_BYTES + 1)
            meta = {
                "company_slug": slug, "url": url, "http_status": response.status,
                "content_type": response.headers.get("Content-Type", ""),
                "body_bytes": len(body), "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": "",
            }
        if len(body) > MAX_BODY_BYTES:
            meta["error"] = "response exceeded size safety limit"
            return meta
        try:
            payload = json.loads(body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            meta["error"] = "response was not valid JSON"
            meta["body_excerpt"] = body[:250].decode("utf-8", "replace")
            return meta
        meta["json_schema"] = inspect_payload(payload)
        return meta
    except urllib.error.HTTPError as exc:
        try:
            excerpt = exc.read(300).decode("utf-8", "replace")
        except Exception:
            excerpt = ""
        return {
            "company_slug": slug, "url": url, "http_status": exc.code,
            "content_type": exc.headers.get("Content-Type", "") if exc.headers else "",
            "body_bytes": len(excerpt), "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": f"HTTPError {exc.code}", "body_excerpt": excerpt,
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "company_slug": slug, "url": url, "http_status": 0, "content_type": "",
            "body_bytes": 0, "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {str(exc)[:220]}",
        }


def main():
    if not RATIO_FILE.is_file():
        raise FileNotFoundError("Fundai ratio snapshot is missing; fetch it before probing history.")
    payload = json.loads(RATIO_FILE.read_text(encoding="utf-8"))
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Fundai ratio snapshot has no usable rows.")
    chosen = []
    seen = set()
    for row in rows:
        slug = str(row.get("company_slug") or "").strip()
        if slug and slug not in seen:
            seen.add(slug)
            chosen.append(slug)
        if len(chosen) >= MAX_SYMBOLS:
            break
    if not chosen:
        raise ValueError("No company_slug values available from the public ratio snapshot.")

    results = []
    for i, slug in enumerate(chosen):
        row = probe(slug)
        results.append(row)
        print(
            f"FUNDAI_HISTORY_PROBE:{slug} HTTP:{row.get('http_status')} "
            f"BYTES:{row.get('body_bytes')} ERROR:{row.get('error', '')}"
        )
        if i < len(chosen) - 1:
            time.sleep(0.25)

    successful = [row for row in results if row.get("http_status") == 200 and "json_schema" in row]
    output = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": BASE_URL,
        "method": "bounded public GET only; no authentication or access-control bypass",
        "symbols_tested": len(results),
        "json_success_count": len(successful),
        "route_schema_usable": any(
            any(a.get("count", 0) > 0 and a.get("first_item_keys") for a in row["json_schema"].get("arrays", []))
            for row in successful
        ),
        "results": results,
        "conclusion": (
            "Public history schema detected; integration still requires period/unit validation."
            if successful else
            "History route did not return usable JSON in the bounded probe; keep it out of scoring."
        ),
    }
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"FUNDAI_HISTORY_PROBE_SUMMARY: tested={len(results)} json_success={len(successful)} route_schema_usable={output['route_schema_usable']}")
    print(f"OUTPUT:{OUTPUT_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
