"""Incremental daily refresh wrapper for fetch_history.py.

Fetches a short recent overlap for each 1D/W/M series and merges those bars with
existing history files. This avoids downloading the entire 2018-to-present series
for every symbol on each market day.
"""
from __future__ import annotations

import json

import fetch_history as fh


_PREVIOUS_MANIFEST: dict = {}
_ORIGINAL_LOAD_MANIFEST = fh.load_previous_manifest
_ORIGINAL_SUMMARIZE_SYMBOL = fh.summarize_symbol
_ORIGINAL_SAVE_MANIFEST = fh.save_manifest


def load_previous_manifest() -> dict:
    global _PREVIOUS_MANIFEST
    payload = _ORIGINAL_LOAD_MANIFEST()
    _PREVIOUS_MANIFEST = payload if isinstance(payload, dict) else {}
    return _PREVIOUS_MANIFEST


def _old_records_by_symbol() -> dict:
    return {
        fh.normalize(item.get("symbol")): item
        for item in (_PREVIOUS_MANIFEST.get("results") or [])
        if isinstance(item, dict) and item.get("symbol")
    }


def _last_existing_time(source_item: dict, resolution: str) -> int | None:
    """Read the actual latest timestamp from a history file, not only manifest metadata."""
    symbol = str(source_item.get("symbol") or "")
    old_record = _old_records_by_symbol().get(fh.normalize(symbol), {})
    intervals = old_record.get("intervals") or {}
    interval = intervals.get(resolution) or {}
    relative_path = interval.get("file")
    if relative_path:
        path = fh.ROOT / relative_path
    else:
        path = fh.HISTORY_DIR / fh.interval_filename(
            fh.safe_slug(symbol, source_item.get("id")), resolution
        )
    if not path.is_file() or path.stat().st_size == 0:
        return None

    # Files are chronological. Scan backward and tolerate a damaged trailing line.
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            position = stream.tell()
            if position == 0:
                return None
            block_size = 4096
            data = b""
            while position > 0 and b"\n" not in data:
                take = min(block_size, position)
                position -= take
                stream.seek(position)
                data = stream.read(take) + data
            for line in reversed(data.splitlines()):
                try:
                    row = json.loads(line.decode("utf-8"))
                    ts = int(row["time"])
                    if ts > 0:
                        return ts
                except (ValueError, TypeError, KeyError, UnicodeDecodeError):
                    continue
    except OSError:
        return None
    return None


def _from_time(source_item: dict, resolution: str) -> int:
    latest = _last_existing_time(source_item, resolution)
    if latest is None:
        return fh.timestamp(fh.START_DATE)
    # A two-week overlap accommodates late candle completion, holidays, and corrections.
    overlap = latest - 14 * 24 * 60 * 60
    return max(fh.timestamp(fh.START_DATE), overlap)


def collect_batch(driver, batch, start_time, end_time):
    requested = []
    for item in batch:
        requested.append({
            "id": item.get("id"),
            "symbol": item.get("symbol"),
            "company": item.get("company"),
            "close_price": item.get("close_price"),
            "change_pct": item.get("change_pct"),
            "area_raw": item.get("area_raw"),
            "fromTimes": {
                resolution: _from_time(item, resolution)
                for resolution in fh.RESOLUTIONS
            },
        })
    return driver.execute_async_script(
        fh.BROWSER_COLLECTOR,
        requested,
        fh.RESOLUTIONS,
        start_time,
        end_time,
        fh.COUNTBACK,
    )


def merge_write_jsonl(path, series, count):
    """Merge fresh candles with the stored series; fresh values win on duplicate timestamps."""
    path = fh.Path(path)
    merged = {}
    if path.is_file():
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                        ts = int(row["time"])
                        if ts > 0:
                            merged[ts] = row
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        continue
        except OSError:
            pass

    required = ("time", "open", "high", "low", "close")
    for key in required:
        if not isinstance(series.get(key), list):
            raise ValueError(f"Missing candle array: {key}")
    volume = series.get("volume")
    if not isinstance(volume, list):
        volume = []
    usable_count = min(max(0, int(count)), *(len(series[key]) for key in required))

    for index in range(usable_count):
        ts = int(series["time"][index])
        if ts <= 0:
            continue
        merged[ts] = {
            "date": fh.date_from_timestamp(ts),
            "time": ts,
            "open": series["open"][index],
            "high": series["high"][index],
            "low": series["low"][index],
            "close": series["close"][index],
            "volume": volume[index] if index < len(volume) else None,
        }

    if not merged:
        raise ValueError("Incremental fetch and existing history contained no valid candles")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        stream.write("\n".join(
            json.dumps(merged[ts], ensure_ascii=False, separators=(",", ":"))
            for ts in sorted(merged)
        ) + "\n")


def summarize_symbol(result, source_item):
    summary = _ORIGINAL_SUMMARIZE_SYMBOL(result, source_item)
    # The API response contains only the overlap window; manifest counts should
    # describe the complete merged file.
    for interval in (summary.get("intervals") or {}).values():
        if interval.get("status") != "success" or not interval.get("file"):
            continue
        path = fh.ROOT / interval["file"]
        if not path.is_file():
            continue
        rows = []
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                        if int(row.get("time") or 0) > 0:
                            rows.append(row)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        continue
        except OSError:
            continue
        if rows:
            interval["count"] = len(rows)
            interval["first_date"] = rows[0].get("date") or fh.date_from_timestamp(rows[0]["time"])
            interval["last_date"] = rows[-1].get("date") or fh.date_from_timestamp(rows[-1]["time"])
            interval["written"] = True
    return summary


def save_manifest(payload, selected_items, summaries, started_at, reused_count, fatal_error=None):
    manifest = _ORIGINAL_SAVE_MANIFEST(
        payload, selected_items, summaries, started_at, reused_count,
        fatal_error=fatal_error,
    )
    manifest["history_update_mode"] = "incremental_recent_overlap_merge"
    manifest["incremental_overlap_calendar_days"] = 14
    manifest["notes"] = [
        "Daily refresh requests only a 14-calendar-day overlap from each existing 1D/W/M history file.",
        "New bars replace rows with the same timestamp; older bars are preserved and merged chronologically.",
        "A symbol without a readable history file is bootstrapped from 2018-01-01.",
        "Failed and partial symbols remain eligible for the next run.",
        "Market-map completeness and full exchange-universe coverage are not independently verified.",
    ]
    fh.write_json(fh.MANIFEST_FILE, manifest)
    return manifest


# The upstream helper treats any successful cache as a permanent hit, regardless
# of freshness. Fetch all symbols but only request/merge the recent overlap.
fh.load_previous_manifest = load_previous_manifest
fh.reusable_successes = lambda items, previous_manifest: {}
fh.collect_batch = collect_batch
fh.write_jsonl = merge_write_jsonl
fh.summarize_symbol = summarize_symbol
fh.save_manifest = save_manifest

old_marker = "from: String(fromTime),"
new_marker = "from: String((requested.fromTimes && requested.fromTimes[resolution]) ?? fromTime),"
if old_marker not in fh.BROWSER_COLLECTOR:
    raise RuntimeError("Could not safely patch the upstream collector from-time expression")
fh.BROWSER_COLLECTOR = fh.BROWSER_COLLECTOR.replace(old_marker, new_marker, 1)


if __name__ == "__main__":
    fh.main()
