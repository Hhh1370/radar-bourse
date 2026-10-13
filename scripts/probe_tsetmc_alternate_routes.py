#!/usr/bin/env python3
"""Test free legacy TSETMC and tse.ir data routes from GitHub Actions.

This is a connectivity/data-shape test only. It uses no paid service and never
turns a blocked endpoint into zero company activity.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "tsetmc_alternate_probe.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"
MAX_BYTES = 2 * 1024 * 1024
CONNECT_TIMEOUT = 4
MAX_TIME = 8


def norm(value):
    value = str(value or "").strip()
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    value = value.translate(table).replace("ي", "ی").replace("ك", "ک").replace("\u200c", "")
    return re.sub(r"[\s\-_]+", "", value).casefold()


def curl_get(url):
    with tempfile.NamedTemporaryFile(prefix="tsetmc-alt-", suffix=".txt") as tmp:
        command = [
            "curl", "--location", "--http1.1",
            "--connect-timeout", str(CONNECT_TIMEOUT),
            "--max-time", str(MAX_TIME),
            "--max-filesize", str(MAX_BYTES),
            "--retry", "0",
            "--user-agent", UA,
            "--header", "Accept: application/json,text/plain,text/html,*/*;q=0.8",
            "--header", "Referer: https://www.tsetmc.com/",
            "--silent", "--show-error",
            "--output", tmp.name,
            "--write-out", "%{http_code}|%{content_type}|%{url_effective}",
            url,
        ]
        start = time.monotonic()
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=MAX_TIME + 3, check=False)
            fields = (result.stdout or "").strip().split("|", 2)
            status = fields[0] if fields and fields[0] else "ERROR"
            try:
                status = int(status)
            except ValueError:
                pass
            content_type = fields[1] if len(fields) > 1 else ""
            effective_url = fields[2] if len(fields) > 2 else url
            tmp.flush()
            size = Path(tmp.name).stat().st_size
            with open(tmp.name, "rb") as file:
                body = file.read(MAX_BYTES + 1)
            error = (result.stderr or "").strip()
            if size > MAX_BYTES or result.returncode == 63:
                error = f"response exceeded {MAX_BYTES}-byte safety cap"
            elif result.returncode and not error:
                error = f"curl exit code {result.returncode}"
            return {
                "url": url, "effective_url": effective_url,
                "http_status": status, "content_type": content_type,
                "body_bytes": len(body), "elapsed_seconds": round(time.monotonic() - start, 2),
                "curl_exit_code": result.returncode, "error": error[:400],
                "text": body.decode("utf-8-sig", "replace"),
            }
        except subprocess.TimeoutExpired:
            tmp.flush()
            try:
                with open(tmp.name, "rb") as file:
                    body = file.read(MAX_BYTES + 1)
            except Exception:
                body = b""
            return {
                "url": url, "effective_url": url, "http_status": 0,
                "content_type": "", "body_bytes": len(body),
                "elapsed_seconds": round(time.monotonic() - start, 2),
                "curl_exit_code": "TIMEOUT",
                "error": f"curl timeout after {MAX_TIME} seconds",
                "text": body.decode("utf-8-sig", "replace"),
            }


def outline(result):
    text = result.get("text", "")
    clean = text.lstrip()
    return {
        k: result[k]
        for k in ("url", "effective_url", "http_status", "content_type",
                  "body_bytes", "elapsed_seconds", "curl_exit_code", "error")
    } | {
        "nonempty_body": bool(clean),
        "starts_with": clean[:90].replace("\n", " "),
        "semicolon_rows": sum(1 for row in text.split(";") if row.strip()),
        "at_segments": len(text.split("@")),
        "json_like": clean.startswith("{") or clean.startswith("["),
        "html_like": "<html" in clean[:500].lower() or "<!doctype" in clean[:500].lower(),
    }


def extract_search_symbols(text):
    items = []
    for line in re.split(r"[;\r\n]+", text):
        fields = [part.strip() for part in line.split(",")]
        if len(fields) < 3:
            continue
        if norm(fields[0]) in {norm("فولاد"), norm("وبملت"), norm("فملی")}:
            items.append({
                "symbol": fields[0], "company": fields[1],
                "ins_code": fields[2],
                "row_fields": len(fields),
                "row_preview": line[:700],
            })
    return items[:20]


def extract_marketwatch_symbols(text):
    items = []
    parts = text.split("@")
    if len(parts) < 3:
        return items
    rows = parts[2].split(";")
    for line in rows:
        fields = [part.strip() for part in line.split(",")]
        if len(fields) >= 4 and norm(fields[2]) in {norm("فولاد"), norm("وبملت"), norm("فملی")}:
            items.append({
                "ins_code": fields[0], "isin": fields[1],
                "symbol": fields[2], "company": fields[3],
                "field_count": len(fields), "row_preview": line[:700],
            })
    return items[:20]


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    urls = {
        "legacy_market_watch": "http://old.tsetmc.com/tsev2/data/MarketWatchInit.aspx?h=0&r=0",
        "legacy_search_foolad": "http://old.tsetmc.com/tsev2/data/search.aspx?skey=" + urllib.parse.quote("فولاد", safe=""),
        "legacy_client_type_all": "http://old.tsetmc.com/tsev2/data/ClientTypeAll.aspx",
        "cdn_search_foolad": "https://cdn.tsetmc.com/api/Instrument/GetInstrumentSearch/" + urllib.parse.quote("فولاد", safe=""),
        "webgw_market_watch": "https://webgw.tse.ir/InstrumentProvider/api/v1/MarketWatch/MarketWatchCash/fa",
    }
    results = {}
    for name, url in urls.items():
        results[name] = curl_get(url)
        print(f"SOURCE:{name} HTTP:{results[name]['http_status']} BYTES:{results[name]['body_bytes']} SECONDS:{results[name]['elapsed_seconds']} ERROR:{results[name]['error']}")
        time.sleep(0.2)

    candidates = []
    candidates.extend(extract_marketwatch_symbols(results["legacy_market_watch"].get("text", "")))
    candidates.extend(extract_search_symbols(results["legacy_search_foolad"].get("text", "")))
    unique = {}
    for item in candidates:
        if item.get("ins_code"):
            unique[item["ins_code"]] = item
    candidates = list(unique.values())

    historical = []
    # Probe only a few actual instrument IDs discovered from a successful response.
    for instrument in candidates[:3]:
        ins_code = instrument["ins_code"]
        url = "http://old.tsetmc.com/tsev2/data/clienttype.aspx?i=" + urllib.parse.quote(ins_code, safe="")
        result = curl_get(url)
        parsed_rows = [row for row in result.get("text", "").split(";") if row.strip()]
        historical.append({
            "instrument": instrument, **outline(result),
            "parsed_row_count": len(parsed_rows),
            "first_row_preview": parsed_rows[0][:700] if parsed_rows else "",
            "last_row_preview": parsed_rows[-1][:700] if parsed_rows else "",
        })
        print(f"LEGACY_CLIENT_TYPE:{instrument.get('symbol')} HTTP:{result['http_status']} BYTES:{result['body_bytes']} ROWS:{len(parsed_rows)} ERROR:{result['error']}")
        time.sleep(0.2)

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose": "Test alternate free TSETMC routes; no paid APIs used.",
        "probes": {name: outline(result) for name, result in results.items()},
        "marketwatch_symbol_matches": extract_marketwatch_symbols(results["legacy_market_watch"].get("text", "")),
        "legacy_search_symbol_matches": extract_search_symbols(results["legacy_search_foolad"].get("text", "")),
        "historical_client_type": historical,
    }
    successful_legacy_history = [
        row for row in historical
        if row.get("http_status") == 200 and row.get("body_bytes", 0) > 50
        and row.get("parsed_row_count", 0) >= 60
    ]
    report["conclusion"] = {
        "legacy_tsetmc_market_watch_ok": results["legacy_market_watch"]["http_status"] == 200 and results["legacy_market_watch"]["body_bytes"] > 50,
        "legacy_tsetmc_search_ok": results["legacy_search_foolad"]["http_status"] == 200 and results["legacy_search_foolad"]["body_bytes"] > 10,
        "webgw_market_watch_ok": results["webgw_market_watch"]["http_status"] == 200 and results["webgw_market_watch"]["body_bytes"] > 50,
        "legacy_history_probe_count": len(historical),
        "legacy_history_60plus_rows_count": len(successful_legacy_history),
        "legacy_history_route_usable_for_expansion": bool(successful_legacy_history),
        "note": "A successful limited probe is not proof of full-market coverage; only structurally parsed history can be adopted.",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("ALTERNATE_TSETMC_PROBE_SUMMARY")
    for key, value in report["conclusion"].items():
        print(f"{key.upper()}:{value}")
    print(f"OUTPUT_JSON:{OUT}")


if __name__ == "__main__":
    main()
