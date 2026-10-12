import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKETMAP = ROOT / "data" / "marketmap.json"
MANIFEST = ROOT / "data" / "history_manifest.json"
OUTPUT = ROOT / "data" / "stock_universe.json"

RESOLUTIONS = ["1D", "W", "M"]


def read_json(path):
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def validate_file(path, expected_count):
    if not path.is_file() or path.stat().st_size == 0:
        return False, 0

    count = 0
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                candle = json.loads(line)
                if not all(
                    key in candle
                    for key in ("date", "open", "high", "low", "close")
                ):
                    return False, count
                count += 1
    except (OSError, json.JSONDecodeError):
        return False, count

    return count == expected_count, count


def main():
    marketmap = read_json(MARKETMAP)
    manifest = read_json(MANIFEST)

    valid = []
    excluded = []

    for item in manifest.get("results", []):
        symbol = item.get("symbol", "")
        resolved = item.get("resolved_symbol") or {}
        intervals = item.get("intervals") or {}

        record = {
            "symbol": symbol,
            "company": item.get("company", ""),
            "marketmap_id": item.get("marketmap_id"),
            "marketmap_close_price": item.get("marketmap_close_price"),
            "marketmap_change_pct": item.get("marketmap_change_pct"),
            "chart_id": resolved.get("canonicalId"),
            "exchange": (
                (item.get("search") or {})
                .get("selected", {})
                .get("exchange")
            ),
            "history": {},
        }

        reason = None

        if item.get("status") != "success":
            reason = "history_not_successful"
        elif not record["chart_id"]:
            reason = "missing_chart_id"
        elif not isinstance(record["marketmap_close_price"], (int, float)):
            reason = "invalid_market_price"
        elif record["marketmap_close_price"] <= 0:
            reason = "non_positive_market_price"

        if not reason:
            for resolution in RESOLUTIONS:
                interval = intervals.get(resolution) or {}
                relative_file = interval.get("file")
                count = interval.get("count", 0)

                if (
                    interval.get("status") != "success"
                    or not relative_file
                    or not isinstance(count, int)
                    or count <= 0
                ):
                    reason = f"invalid_{resolution}_history"
                    break

                path = ROOT / relative_file
                ok, actual_count = validate_file(path, count)

                if not ok:
                    reason = f"invalid_file_{resolution}"
                    break

                record["history"][resolution] = {
                    "file": relative_file,
                    "count": actual_count,
                    "first_date": interval.get("first_date"),
                    "last_date": interval.get("last_date"),
                }

        if reason:
            excluded.append({
                "symbol": symbol,
                "company": item.get("company", ""),
                "reason": reason,
            })
        else:
            valid.append(record)

    valid.sort(key=lambda x: x["symbol"])

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Rahavard market map + Nahayatnegar history",
        "marketmap_records": len(marketmap.get("items", [])),
        "marketmap_completeness_verified": False,
        "valid_stock_count": len(valid),
        "excluded_record_count": len(excluded),
        "resolutions": RESOLUTIONS,
        "history_start_requested": manifest.get(
            "history_start_requested"
        ),
        "coverage_note": (
            "This list contains records with validated stock history. "
            "It does not independently prove coverage of every active "
            "TSE and IFB security."
        ),
        "stocks": valid,
        "excluded_records": excluded,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("Market map records:", result["marketmap_records"])
    print("Validated stocks:", result["valid_stock_count"])
    print("Excluded records:", result["excluded_record_count"])
    print("Output:", OUTPUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
