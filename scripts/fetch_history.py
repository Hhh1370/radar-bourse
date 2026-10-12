import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


ROOT = Path(__file__).resolve().parents[1]
MARKETMAP_FILE = ROOT / "data" / "marketmap.json"
HISTORY_DIR = ROOT / "data" / "history"
MANIFEST_FILE = ROOT / "data" / "history_manifest.json"

CHART_URL = "https://www.nahayatnegar.com/tv/IRO1KSHJ00013"
START_DATE = "2018-01-01"

# مرحلهٔ بعد از آزمایش موفق ۵۰ نماد
MAX_SYMBOLS = 150
BATCH_SIZE = 5
MAX_BATCH_RETRIES = 1
COUNTBACK = 5000
RESOLUTIONS = ["1D", "W", "M"]

PRIORITY_SYMBOLS = [
    "فملی", "کچاد", "فولاد", "حکشتی", "شستا",
    "خودرو", "خساپا", "کگل", "پارسان", "کگهر",
]

SLUGS = {
    "فملی": "fmeli",
    "کچاد": "kchad",
    "فولاد": "foolad",
    "حکشتی": "hekshati",
    "شستا": "shasta",
    "خودرو": "khodro",
    "خساپا": "khsapa",
    "کگل": "kgol",
    "پارسان": "parsan",
    "کگهر": "kgohar",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(date_text):
    value = datetime.strptime(date_text, "%Y-%m-%d")
    return int(value.replace(tzinfo=timezone.utc).timestamp())


def normalize(value):
    return (
        str(value or "")
        .replace("ي", "ی")
        .replace("ك", "ک")
        .replace("\u200c", "")
        .replace("\u200f", "")
        .replace("\u200e", "")
        .replace(" ", "")
        .strip()
    )


def safe_slug(symbol, marketmap_id):
    if symbol in SLUGS:
        return SLUGS[symbol]

    cleaned = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        str(marketmap_id or ""),
    )
    return f"symbol_{cleaned or 'unknown'}"


def load_marketmap():
    if not MARKETMAP_FILE.exists():
        raise FileNotFoundError(
            f"Market map not found: {MARKETMAP_FILE}"
        )

    payload = json.loads(
        MARKETMAP_FILE.read_text(encoding="utf-8")
    )
    items = payload.get("items", [])

    if not isinstance(items, list) or not items:
        raise ValueError("No usable items in marketmap.json")

    return payload, items


def select_symbols(items):
    by_symbol = {
        normalize(item.get("symbol")): item
        for item in items
        if item.get("symbol")
    }

    chosen, seen = [], set()

    for name in PRIORITY_SYMBOLS:
        item = by_symbol.get(normalize(name))
        if item is None:
            continue

        key = normalize(item.get("symbol"))
        if key and key not in seen:
            chosen.append(item)
            seen.add(key)

    remaining = [
        item for item in items
        if normalize(item.get("symbol")) not in seen
    ]
    remaining.sort(
        key=lambda item: float(item.get("area_raw") or 0),
        reverse=True,
    )

    for item in remaining:
        key = normalize(item.get("symbol"))
        if not key or key in seen:
            continue
        chosen.append(item)
        seen.add(key)

        if len(chosen) >= MAX_SYMBOLS:
            break

    return chosen[:MAX_SYMBOLS]


def make_driver():
    options = Options()

    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin and Path(chrome_bin).exists():
        options.binary_location = chrome_bin

    for argument in [
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--window-size=1440,1000",
        "--lang=fa-IR",
    ]:
        options.add_argument(argument)

    driver_path = os.environ.get("CHROMEDRIVER")
    if driver_path and Path(driver_path).is_file():
        driver = webdriver.Chrome(
            service=Service(executable_path=driver_path),
            options=options,
        )
    else:
        driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(60)
    driver.set_script_timeout(90)
    return driver


BROWSER_COLLECTOR = r"""
const requestedItems = arguments[0];
const resolutions = arguments[1];
const fromTime = arguments[2];
const toTime = arguments[3];
const countback = arguments[4];
const done = arguments[arguments.length - 1];

(async function () {
    const output = {symbols: [], errors: []};

    const delay = ms =>
        new Promise(resolve => setTimeout(resolve, ms));

    async function getJson(path) {
        await delay(120);

        const response = await fetch(path, {
            method: "GET",
            credentials: "same-origin",
            headers: {"Accept": "application/json"}
        });

        const text = await response.text();
        let data = null;

        try {
            data = JSON.parse(text);
        } catch (_) {}

        return {
            httpStatus: response.status,
            data: data,
            bodyPreview: text.slice(0, 400)
        };
    }

    function norm(value) {
        return String(value || "")
            .replace(/ي/g, "ی")
            .replace(/ك/g, "ک")
            .replace(/\u200c/g, "")
            .replace(/\u200f/g, "")
            .replace(/\u200e/g, "")
            .replace(/\s+/g, "")
            .trim();
    }

    function getSymbolObject(data) {
        if (!data || typeof data !== "object") return null;
        if (data.tvSymbol && typeof data.tvSymbol === "object") {
            return data.tvSymbol;
        }
        if (data.id || data.ticker || data.full_name) return data;
        return null;
    }

    for (const requested of requestedItems) {
        const item = {
            marketmap: requested,
            status: "failed",
            search: null,
            resolvedSymbol: null,
            histories: {},
            errors: []
        };

        try {
            let selected = null;
            let candidates = [];

            for (const exchange of ["بورس", ""]) {
                const searchParams = new URLSearchParams({
                    query: requested.symbol,
                    exchange: exchange,
                    type: "stock",
                    limit: "50"
                });

                const response = await getJson(
                    "/tv/chart/search?" + searchParams.toString()
                );

                candidates = Array.isArray(response.data?.tvSymbols)
                    ? response.data.tvSymbols
                    : [];

                const expected = norm(requested.symbol + "1");

                selected = candidates.find(symbol =>
                    symbol &&
                    symbol.id &&
                    norm(symbol.name || symbol.symbol) === expected &&
                    (!symbol.type || symbol.type === "stock") &&
                    (
                        !exchange ||
                        !symbol.exchange ||
                        symbol.exchange === exchange
                    )
                ) || null;

                item.search = {
                    httpStatus: response.httpStatus,
                    exchange: exchange,
                    resultCount: candidates.length,
                    selected: selected ? {
                        id: selected.id,
                        name: selected.name || selected.symbol || null,
                        full_name: selected.full_name ||
                            selected.description || null,
                        type: selected.type || "stock",
                        exchange: selected.exchange || null
                    } : null
                };

                if (selected) break;
            }

            if (!selected) {
                item.errors.push(
                    "Exact ordinary-stock symbol not found."
                );
                output.symbols.push(item);
                await delay(200);
                continue;
            }

            const rawId = String(selected.id);
            const canonicalId = rawId.endsWith("3")
                ? rawId
                : rawId + "3";

            const symbolParams = new URLSearchParams({
                symbol: canonicalId
            });

            const symbolResponse = await getJson(
                "/tv/chart/symbols?" + symbolParams.toString()
            );

            const resolved = getSymbolObject(symbolResponse.data);

            item.resolvedSymbol = {
                rawSearchId: rawId,
                canonicalId: canonicalId,
                httpStatus: symbolResponse.httpStatus,
                id: resolved?.id || null,
                ticker: resolved?.ticker || null,
                name: resolved?.name || resolved?.symbol || null,
                full_name: resolved?.full_name || null,
                type: resolved?.type || null
            };

            if (
                symbolResponse.httpStatus !== 200 ||
                !resolved ||
                String(resolved.id || resolved.ticker) !== canonicalId
            ) {
                item.errors.push(
                    "Canonical chart symbol ID was not confirmed."
                );
                output.symbols.push(item);
                await delay(200);
                continue;
            }

            for (const resolution of resolutions) {
                const params = new URLSearchParams({
                    symbol: canonicalId,
                    resolution: resolution,
                    from: String(fromTime),
                    to: String(toTime),
                    type: resolved.type || selected.type || "stock",
                    adjustmentType: "3",
                    countback: String(countback)
                });

                try {
                    const response = await getJson(
                        "/tv/chart/history?" + params.toString()
                    );

                    const data = response.data || {};
                    const arrays = {
                        time: data.t,
                        open: data.o,
                        high: data.h,
                        low: data.l,
                        close: data.c,
                        volume: data.v
                    };

                    const lengths = {};
                    for (const [key, value] of Object.entries(arrays)) {
                        lengths[key] = Array.isArray(value)
                            ? value.length : 0;
                    }

                    const required = [
                        "time", "open", "high", "low", "close"
                    ];

                    const count = required.every(
                        key => Array.isArray(arrays[key])
                    )
                        ? Math.min(...required.map(key => lengths[key]))
                        : 0;

                    const apiStatus = data.s || data.status || null;
                    const success =
                        response.httpStatus === 200 &&
                        apiStatus === "ok" &&
                        count > 0;

                    item.histories[resolution] = {
                        status: success ? "success" : "failed",
                        httpStatus: response.httpStatus,
                        apiStatus: apiStatus,
                        errorMessage: data.errmsg || data.error || null,
                        arrayLengths: lengths,
                        count: count,
                        firstTime: count ? arrays.time[0] : null,
                        lastTime: count ? arrays.time[count - 1] : null,
                        series: success ? {
                            time: arrays.time.slice(0, count),
                            open: arrays.open.slice(0, count),
                            high: arrays.high.slice(0, count),
                            low: arrays.low.slice(0, count),
                            close: arrays.close.slice(0, count),
                            volume: Array.isArray(arrays.volume)
                                ? arrays.volume.slice(0, count) : []
                        } : null,
                        bodyPreview: success ? null : response.bodyPreview
                    };
                } catch (error) {
                    item.histories[resolution] = {
                        status: "failed",
                        error: String(error)
                    };
                }
            }

            const successes = Object.values(item.histories)
                .filter(history => history.status === "success").length;

            item.status = successes === resolutions.length
                ? "success"
                : successes > 0 ? "partial" : "failed";

            if (successes === 0) {
                item.errors.push("No usable history interval returned.");
            }
        } catch (error) {
            item.errors.push(String(error));
        }

        output.symbols.push(item);
        await delay(200);
    }

    done(output);
})().catch(error => done({fatalError: String(error)}));
"""


def date_from_timestamp(value):
    if value is None:
        return None

    try:
        return datetime.fromtimestamp(
            int(value), timezone.utc
        ).date().isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path, series, count):
    required = ["time", "open", "high", "low", "close"]

    for key in required:
        if not isinstance(series.get(key), list):
            raise ValueError(f"Missing candle array: {key}")

    volume = series.get("volume")
    if not isinstance(volume, list):
        volume = []

    lines = []

    for index in range(count):
        candle = {
            "date": date_from_timestamp(series["time"][index]),
            "time": series["time"][index],
            "open": series["open"][index],
            "high": series["high"][index],
            "low": series["low"][index],
            "close": series["close"][index],
            "volume": volume[index] if index < len(volume) else None,
        }
        lines.append(json.dumps(
            candle, ensure_ascii=False, separators=(",", ":")
        ))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(lines) + ("\n" if lines else ""),
        encoding="utf-8",
    )


def interval_filename(slug, resolution):
    return f"{slug}_{resolution}.jsonl"


def summarize_symbol(result, source_item):
    symbol = source_item.get("symbol", "")
    slug = safe_slug(symbol, source_item.get("id"))

    summary = {
        "symbol": symbol,
        "company": source_item.get("company", ""),
        "marketmap_id": source_item.get("id"),
        "marketmap_close_price": source_item.get("close_price"),
        "marketmap_change_pct": source_item.get("change_pct"),
        "status": result.get("status", "failed"),
        "search": result.get("search"),
        "resolved_symbol": result.get("resolvedSymbol"),
        "intervals": {},
        "errors": result.get("errors", []),
    }

    histories = result.get("histories") or {}

    for resolution in RESOLUTIONS:
        history = histories.get(resolution) or {}
        interval = {
            "status": history.get("status", "failed"),
            "http_status": history.get("httpStatus"),
            "api_status": history.get("apiStatus"),
            "count": history.get("count", 0),
            "first_date": date_from_timestamp(history.get("firstTime")),
            "last_date": date_from_timestamp(history.get("lastTime")),
            "error": history.get("error") or history.get("errorMessage"),
            "written": False,
        }

        if (
            interval["status"] == "success" and
            isinstance(history.get("series"), dict)
        ):
            filename = interval_filename(slug, resolution)
            output_path = HISTORY_DIR / filename

            try:
                write_jsonl(
                    output_path,
                    history["series"],
                    int(history.get("count") or 0),
                )
                interval["file"] = f"data/history/{filename}"
                interval["written"] = True
            except Exception as error:
                interval["status"] = "failed_to_write"
                interval["error"] = str(error)

        summary["intervals"][resolution] = interval

    written = sum(
        1 for item in summary["intervals"].values()
        if item.get("written")
    )

    summary["status"] = (
        "success" if written == len(RESOLUTIONS)
        else "partial" if written > 0
        else "failed"
    )

    return summary


def build_manifest(payload, selected_items, summaries, started_at, fatal_error=None):
    successful = sum(
        1 for item in summaries if item["status"] == "success"
    )
    partial = sum(
        1 for item in summaries if item["status"] == "partial"
    )
    failed = sum(
        1 for item in summaries if item["status"] == "failed"
    )
    files_written = sum(
        1
        for item in summaries
        for interval in item.get("intervals", {}).values()
        if interval.get("written")
    )

    complete = (
        fatal_error is None and
        len(summaries) == len(selected_items) and
        successful == len(selected_items)
    )

    return {
        "source": "Nahayatnegar public chart interface",
        "started_at_utc": started_at,
        "updated_at_utc": utc_now(),
        "marketmap_captured_at_utc": payload.get("captured_at_utc"),
        "marketmap_completeness_verified":
            payload.get("completeness_verified", False),
        "marketmap_count": payload.get("count"),
        "history_start_requested": START_DATE,
        "countback_requested": COUNTBACK,
        "resolutions_requested": RESOLUTIONS,
        "batch_size": BATCH_SIZE,
        "batch_retries": MAX_BATCH_RETRIES,
        "requested_symbols": len(selected_items),
        "processed_symbols": len(summaries),
        "successful_symbols": successful,
        "partial_symbols": partial,
        "failed_symbols": failed,
        "successful_interval_files": files_written,
        "status": (
            "error" if fatal_error
            else "success" if complete
            else "partial"
        ),
        "results": summaries,
        "fatal_error": fatal_error,
        "notes": [
            "150-symbol scaling test; not a full-market scan.",
            "Market-map coverage is not independently verified.",
            "Only status=ok with non-empty candle arrays counts as successful.",
            "Candle dates are derived from UTC timestamps.",
            "The source may revise historical candles.",
        ],
    }


def save_manifest(payload, selected_items, summaries, started_at, fatal_error=None):
    manifest = build_manifest(
        payload, selected_items, summaries, started_at, fatal_error
    )
    write_json(MANIFEST_FILE, manifest)
    return manifest


def open_chart():
    driver = make_driver()
    try:
        driver.get(CHART_URL)
        return driver
    except Exception:
        driver.quit()
        raise


def collect_batch(driver, batch, start_time, end_time):
    requested = [
        {
            "id": item.get("id"),
            "symbol": item.get("symbol"),
            "company": item.get("company"),
            "close_price": item.get("close_price"),
            "change_pct": item.get("change_pct"),
            "area_raw": item.get("area_raw"),
        }
        for item in batch
    ]

    return driver.execute_async_script(
        BROWSER_COLLECTOR,
        requested,
        RESOLUTIONS,
        start_time,
        end_time,
        COUNTBACK,
    )


def failed_result(source_item, message):
    return {
        "marketmap": {
            "id": source_item.get("id"),
            "symbol": source_item.get("symbol"),
            "company": source_item.get("company"),
            "close_price": source_item.get("close_price"),
            "change_pct": source_item.get("change_pct"),
        },
        "status": "failed",
        "errors": [message],
        "histories": {},
    }


def main():
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    started_at = utc_now()
    payload = {}
    selected_items = []
    summaries_by_symbol = {}
    fatal_error = None
    driver = None

    try:
        payload, market_items = load_marketmap()
        selected_items = select_symbols(market_items)

        print("Market map records:", len(market_items))
        print("Selected for this batch:", len(selected_items))
        print("Batch size:", BATCH_SIZE)
        print("Symbols:", ", ".join(
            item.get("symbol", "") for item in selected_items
        ))

        if not selected_items:
            raise ValueError("No symbols selected from marketmap.json")

        start_time = timestamp(START_DATE)
        end_time = int(datetime.now(timezone.utc).timestamp()) + 86400
        total_batches = (
            len(selected_items) + BATCH_SIZE - 1
        ) // BATCH_SIZE

        for batch_index, start_index in enumerate(
            range(0, len(selected_items), BATCH_SIZE),
            start=1,
        ):
            batch = selected_items[start_index:start_index + BATCH_SIZE]
            names = ", ".join(item.get("symbol", "") for item in batch)

            print(f"\nBatch {batch_index}/{total_batches}: {names}")

            batch_result = None
            last_error = None

            for attempt in range(MAX_BATCH_RETRIES + 1):
                try:
                    if driver is None:
                        driver = open_chart()

                    batch_result = collect_batch(
                        driver, batch, start_time, end_time
                    )
                    last_error = None
                    break

                except Exception as error:
                    last_error = repr(error)
                    print(
                        f"Batch attempt {attempt + 1} failed:",
                        last_error,
                    )

                    if driver is not None:
                        try:
                            driver.quit()
                        except Exception:
                            pass
                        driver = None

                    if attempt < MAX_BATCH_RETRIES:
                        time.sleep(3)

            if batch_result is None:
                for source_item in batch:
                    key = normalize(source_item.get("symbol"))
                    failed = failed_result(
                        source_item,
                        f"Batch execution failed: {last_error}",
                    )
                    summaries_by_symbol[key] = summarize_symbol(
                        failed, source_item
                    )
            else:
                result_map = {
                    normalize(
                        (entry.get("marketmap") or {}).get("symbol")
                    ): entry
                    for entry in batch_result.get("symbols", [])
                }

                for source_item in batch:
                    symbol = source_item.get("symbol", "")
                    key = normalize(symbol)
                    entry = result_map.get(key)

                    if entry is None:
                        entry = failed_result(
                            source_item,
                            "No result returned by collector for symbol.",
                        )

                    summary = summarize_symbol(entry, source_item)
                    summaries_by_symbol[key] = summary

                    print(
                        "  ", symbol,
                        "|", summary["status"],
                        "| ID:",
                        (summary.get("resolved_symbol") or {}).get(
                            "canonicalId", "-"
                        ),
                    )

                    for resolution in RESOLUTIONS:
                        interval = summary["intervals"][resolution]
                        print(
                            "    ", resolution,
                            "|", interval["status"],
                            "| candles:", interval.get("count", 0),
                            "| file:", interval.get("file", "-"),
                        )

            # پس از هر دسته ذخیره می‌کنیم؛ شکست دستهٔ بعدی
            # خروجی دسته‌های قبلی را از بین نمی‌برد.
            ordered = [
                summaries_by_symbol[normalize(item.get("symbol"))]
                for item in selected_items
                if normalize(item.get("symbol")) in summaries_by_symbol
            ]
            save_manifest(payload, selected_items, ordered, started_at)

        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
            driver = None

    except Exception as error:
        fatal_error = repr(error)
        print("COLLECTION ERROR:", fatal_error)

    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

        ordered = [
            summaries_by_symbol[normalize(item.get("symbol"))]
            for item in selected_items
            if normalize(item.get("symbol")) in summaries_by_symbol
        ]

        manifest = save_manifest(
            payload,
            selected_items,
            ordered,
            started_at,
            fatal_error=fatal_error,
        )

        print("\n" + "=" * 18 + " FINAL SUMMARY " + "=" * 18)
        print("Requested:", manifest["requested_symbols"])
        print("Processed:", manifest["processed_symbols"])
        print("Successful:", manifest["successful_symbols"])
        print("Partial:", manifest["partial_symbols"])
        print("Failed:", manifest["failed_symbols"])
        print("Saved interval files:", manifest["successful_interval_files"])
        print("Manifest:", MANIFEST_FILE.relative_to(ROOT))
        print("Run status:", manifest["status"])

    if manifest["successful_interval_files"] == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
