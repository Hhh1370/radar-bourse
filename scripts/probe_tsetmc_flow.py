#!/usr/bin/env python3
"""Diagnostic probe for direct TSETMC instrument/client-type endpoints.

This is deliberately a small connectivity/data-shape test, not a market-wide scan.
No paid API or BRS_API_KEY is used.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "tsetmc_flow_probe.json"
BASE = "https://cdn.tsetmc.com/api"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
SYMBOLS = ["فولاد", "وبملت", "فملی", "آباد"]
DATES = ["14050720", "14050719", "14050718"]
TIMEOUT = 12


def clean(value):
    text = str(value or "").strip()
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    text = text.translate(table).replace("ي", "ی").replace("ك", "ک")
    return re.sub(r"[\s‌\-_]+", "", text).casefold()


def request_json(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://www.tsetmc.com/",
            "Origin": "https://www.tsetmc.com",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read(5 * 1024 * 1024 + 1)
            elapsed = round(time.monotonic() - started, 3)
            if len(body) > 5 * 1024 * 1024:
                body = body[:5 * 1024 * 1024]
                too_large = True
            else:
                too_large = False
            decoded = body.decode("utf-8-sig", "replace")
            try:
                payload = json.loads(decoded)
                parse_error = ""
            except Exception as exc:
                payload = None
                parse_error = f"{type(exc).__name__}: {exc}"
            return {
                "http_status": resp.status,
                "content_type": resp.headers.get("Content-Type", ""),
                "body_bytes": len(body),
                "elapsed_seconds": elapsed,
                "json_parsed": payload is not None,
                "parse_error": parse_error,
                "too_large": too_large,
                "payload": payload,
                "error": "",
            }
    except urllib.error.HTTPError as exc:
        return {
            "http_status": exc.code, "content_type": "",
            "body_bytes": 0, "elapsed_seconds": round(time.monotonic() - started, 3),
            "json_parsed": False, "parse_error": "", "too_large": False,
            "payload": None, "error": f"HTTPError {exc.code}: {exc.reason}",
        }
    except Exception as exc:
        return {
            "http_status": 0, "content_type": "",
            "body_bytes": 0, "elapsed_seconds": round(time.monotonic() - started, 3),
            "json_parsed": False, "parse_error": "", "too_large": False,
            "payload": None, "error": f"{type(exc).__name__}: {exc}"[:350],
        }


def walk_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_objects(child)


def extract_instruments(payload, requested_symbol):
    expected = clean(requested_symbol)
    matches = []
    for obj in walk_objects(payload):
        code = obj.get("insCode") or obj.get("InsCode") or obj.get("inscode")
        if code is None:
            continue
        short = obj.get("lVal18AFC") or obj.get("symbol") or obj.get("Symbol") or obj.get("name")
        if not short:
            continue
        normalized_short = clean(short)
        if normalized_short == expected or normalized_short.rstrip("0123456789") == expected:
            matches.append({
                "symbol": str(short),
                "company": obj.get("lVal30") or obj.get("companyName") or obj.get("name") or "",
                "ins_code": str(code),
                "isin": obj.get("cIsin") or obj.get("isin") or "",
                "market": obj.get("flowTitle") or obj.get("cgrValCotTitle") or "",
                "raw_keys": sorted(str(k) for k in obj.keys())[:35],
            })
    unique = {}
    for item in matches:
        unique[item["ins_code"]] = item
    return list(unique.values())


def payload_outline(payload):
    if isinstance(payload, dict):
        result = {"type": "object", "keys": list(payload.keys())[:40]}
        for key, value in payload.items():
            if isinstance(value, list):
                result[f"{key}_count"] = len(value)
                if value and isinstance(value[0], dict):
                    result[f"{key}_first_keys"] = list(value[0].keys())[:30]
            elif isinstance(value, dict):
                result[f"{key}_keys"] = list(value.keys())[:30]
        return result
    if isinstance(payload, list):
        return {"type": "array", "count": len(payload), "first_keys": list(payload[0].keys())[:30] if payload and isinstance(payload[0], dict) else []}
    return {"type": type(payload).__name__}


def compact_result(result):
    return {k: v for k, v in result.items() if k != "payload"}


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose": "Direct TSETMC flow endpoint probe; no paid API",
        "sources": [],
        "symbols": [],
    }

    # Probe market-wide latest-day client-type snapshot first.
    url = f"{BASE}/ClientType/GetClientTypeAll"
    result = request_json(url)
    report["sources"].append({
        "name": "ClientTypeAll",
        "url": url,
        **compact_result(result),
        "payload_outline": payload_outline(result["payload"]) if result["payload"] is not None else {},
    })
    if result["payload"] is not None:
        time.sleep(0.2)

    for symbol in SYMBOLS:
        search_url = f"{BASE}/Instrument/GetInstrumentSearch/{urllib.parse.quote(symbol, safe='')}"
        search = request_json(search_url)
        symbol_row = {
            "requested_symbol": symbol,
            "search_url": search_url,
            "search": compact_result(search),
            "search_payload_outline": payload_outline(search["payload"]) if search["payload"] is not None else {},
            "matches": extract_instruments(search["payload"], symbol) if search["payload"] is not None else [],
            "clienttype_tests": [],
            "closing_price_test": {},
        }
        if not symbol_row["matches"] and search["payload"] is not None:
            # Some endpoint versions use alternate short-name fields. Keep candidates for review.
            candidates = []
            for obj in walk_objects(search["payload"]):
                code = obj.get("insCode") or obj.get("InsCode")
                if code:
                    candidates.append({
                        "symbol": obj.get("lVal18AFC") or obj.get("symbol") or "",
                        "company": obj.get("lVal30") or "",
                        "ins_code": str(code),
                        "isin": obj.get("cIsin") or "",
                        "raw_keys": sorted(str(k) for k in obj.keys())[:25],
                    })
            symbol_row["search_candidates_for_manual_review"] = candidates[:10]

        if symbol_row["matches"]:
            match = symbol_row["matches"][0]
            ins_code = match["ins_code"]
            close_url = f"{BASE}/ClosingPrice/GetClosingPriceDailyList/{urllib.parse.quote(ins_code, safe='')}/5"
            close_result = request_json(close_url)
            symbol_row["closing_price_test"] = {
                "url": close_url,
                **compact_result(close_result),
                "payload_outline": payload_outline(close_result["payload"]) if close_result["payload"] is not None else {},
            }
            time.sleep(0.15)
            for date in DATES:
                history_url = f"{BASE}/ClientType/GetClientTypeHistory/{urllib.parse.quote(ins_code, safe='')}/{date}"
                history = request_json(history_url)
                symbol_row["clienttype_tests"].append({
                    "date": date,
                    "url": history_url,
                    **compact_result(history),
                    "payload_outline": payload_outline(history["payload"]) if history["payload"] is not None else {},
                })
                time.sleep(0.15)
            current_url = f"{BASE}/ClientType/GetClientType/{urllib.parse.quote(ins_code, safe='')}/1/0"
            current = request_json(current_url)
            symbol_row["current_clienttype_test"] = {
                "url": current_url,
                **compact_result(current),
                "payload_outline": payload_outline(current["payload"]) if current["payload"] is not None else {},
            }
        report["symbols"].append(symbol_row)
        time.sleep(0.2)

    successful_history = [
        (s["requested_symbol"], x["date"])
        for s in report["symbols"]
        for x in s.get("clienttype_tests", [])
        if x.get("http_status") == 200 and x.get("json_parsed") and x.get("body_bytes", 0) > 0
    ]
    matched_symbols = [s["requested_symbol"] for s in report["symbols"] if s.get("matches")]
    report["conclusion"] = {
        "clienttype_all_json_success": any(
            s["name"] == "ClientTypeAll" and s.get("http_status") == 200 and s.get("json_parsed")
            for s in report["sources"]
        ),
        "symbols_mapped": matched_symbols,
        "symbol_match_count": len(matched_symbols),
        "historical_clienttype_success_count": len(successful_history),
        "historical_clienttype_success_examples": successful_history[:12],
        "direct_flow_data_usable_for_next_stage": bool(matched_symbols and successful_history),
        "limitations": [
            "Only four instruments and three trading dates were tested.",
            "Endpoint is community-documented and may change without notice.",
            "A single successful daily response does not prove full 12-month coverage.",
            "This probe is not a market-wide scan and does not assign investment scores.",
        ],
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("TSETMC_FLOW_PROBE_SUMMARY")
    print(f"CLIENTTYPE_ALL_SUCCESS:{report['conclusion']['clienttype_all_json_success']}")
    print(f"SYMBOLS_MAPPED:{report['conclusion']['symbol_match_count']}/{len(SYMBOLS)}")
    print(f"MAPPED_SYMBOL_NAMES:{','.join(matched_symbols)}")
    print(f"HISTORICAL_CLIENTTYPE_SUCCESS:{len(successful_history)}/{len(SYMBOLS) * len(DATES)}")
    for symbol in report["symbols"]:
        m = symbol.get("matches", [])
        print(f"SYMBOL:{symbol['requested_symbol']} MAPPED:{'yes' if m else 'no'} INS_CODE:{m[0]['ins_code'] if m else 'n/a'}")
        for test in symbol.get("clienttype_tests", []):
            print(f"  DATE:{test['date']} HTTP:{test['http_status']} BYTES:{test['body_bytes']} JSON:{test['json_parsed']} ERROR:{test['error']}")
    print(f"OUTPUT_JSON:{OUTPUT}")
    if not report["conclusion"]["direct_flow_data_usable_for_next_stage"]:
        print("DECISION: endpoint not verified; do not replace existing flow source yet.")
    else:
        print("DECISION: limited direct-flow access confirmed; extend coverage only after checking response fields and date coverage.")


if __name__ == "__main__":
    main()
