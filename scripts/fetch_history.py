import json
import os
import re
import sys
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
MAX_SYMBOLS = 50
COUNTBACK = 5000
RESOLUTIONS = ["1D", "W", "M"]

# ابتدا این نمادها بررسی می‌شوند؛ سپس فهرست بر اساس اندازهٔ
# تقریبی کاشی‌های نقشهٔ بازار تکمیل می‌شود.
PRIORITY_SYMBOLS = [
    "فملی",
    "کچاد",
    "فولاد",
    "حکشتی",
    "شستا",
    "خودرو",
    "خساپا",
    "کگل",
    "پارسان",
    "کگهر",
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


def to_timestamp(date_text):
    value = datetime.strptime(date_text, "%Y-%m-%d")
    return int(value.replace(tzinfo=timezone.utc).timestamp())


def normalize_symbol(value):
    value = str(value or "")
    return (
        value.replace("ي", "ی")
        .replace("ك", "ک")
        .replace("\u200c", "")
        .replace("\u200f", "")
        .replace("\u200e", "")
        .replace(" ", "")
        .strip()
    )


def safe_slug(symbol, marketmap_id):
    known_slug = SLUGS.get(symbol)
    if known_slug:
        return known_slug

    cleaned = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        str(marketmap_id or "")
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
        raise ValueError(
            "marketmap.json has no usable items array."
        )

    return payload, items


def select_symbols(items):
    """Select up to MAX_SYMBOLS, preferring test symbols."""
    by_symbol = {
        normalize_symbol(item.get("symbol")): item
        for item in items
        if item.get("symbol")
    }

    chosen = []
    seen = set()

    for name in PRIORITY_SYMBOLS:
        item = by_symbol.get(normalize_symbol(name))
        if item is None:
            continue

        key = normalize_symbol(item.get("symbol"))
        if not key or key in seen:
            continue

        chosen.append(item)
        seen.add(key)

        if len(chosen) >= MAX_SYMBOLS:
            return chosen

    remaining = [
        item for item in items
        if normalize_symbol(item.get("symbol")) not in seen
    ]
    remaining.sort(
        key=lambda item: float(item.get("area_raw") or 0),
        reverse=True,
    )

    for item in remaining:
        key = normalize_symbol(item.get("symbol"))

        if not key or key in seen:
            continue

        chosen.append(item)
        seen.add(key)

        if len(chosen) >= MAX_SYMBOLS:
            break

    return chosen


def make_driver():
    options = Options()

    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin and Path(chrome_bin).exists():
        options.binary_location = chrome_bin

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1440,1000")
    options.add_argument("--lang=fa-IR")

    driver_path = os.environ.get("CHROMEDRIVER")
    if driver_path and Path(driver_path).is_file():
        service = Service(executable_path=driver_path)
        driver = webdriver.Chrome(
            service=service,
            options=options
        )
    else:
        driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(60)
    # 50 نماد با تاریخچهٔ سه بازه ممکن است زمان‌بر باشد.
    driver.set_script_timeout(600)
    return driver


BROWSER_COLLECTOR = r"""
const requestedItems = arguments[0];
const resolutions = arguments[1];
const fromTime = arguments[2];
const toTime = arguments[3];
const countback = arguments[4];
const done = arguments[arguments.length - 1];

(async function () {
    const result = {
        capturedAtUtc: new Date().toISOString(),
        config: null,
        symbols: [],
        errors: []
    };

    // فاصلهٔ کوتاه بین درخواست‌ها برای کاهش فشار بر سرور.
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
        let parseError = null;

        try {
            data = JSON.parse(text);
        } catch (error) {
            parseError = String(error);
        }

        return {
            httpStatus: response.status,
            data: data,
            parseError: parseError,
            bodyPreview: text.slice(0, 700)
        };
    }

    function normalize(value) {
        return String(value || "")
            .replace(/ي/g, "ی")
            .replace(/ك/g, "ک")
            .replace(/\u200c/g, "")
            .replace(/\u200f/g, "")
            .replace(/\u200e/g, "")
            .replace(/\s+/g, "")
            .trim();
    }

    function symbolObject(data) {
        if (!data || typeof data !== "object") return null;

        if (
            data.tvSymbol &&
            typeof data.tvSymbol === "object"
        ) {
            return data.tvSymbol;
        }

        if (data.id || data.ticker || data.full_name) {
            return data;
        }

        return null;
    }

    function countArrays(data) {
        const result = {};

        for (const [key, value] of Object.entries(data || {})) {
            if (Array.isArray(value)) {
                result[key] = value.length;
            }
        }

        return result;
    }

    try {
        const configResponse =
            await getJson("/tv/chart/config");

        result.config = {
            httpStatus: configResponse.httpStatus,
            supportsSearch:
                configResponse.data?.supports_search ?? null,
            supportedResolutions:
                configResponse.data?.supported_resolutions || []
        };

        for (const requested of requestedItems) {
            const itemResult = {
                marketmap: requested,
                status: "failed",
                search: null,
                resolvedSymbol: null,
                histories: {},
                errors: []
            };

            try {
                let selected = null;
                let lastSearch = null;

                const attempts = [
                    {exchange: "بورس", type: "stock"},
                    {exchange: "", type: "stock"}
                ];

                for (const attempt of attempts) {
                    const searchParams = new URLSearchParams({
                        query: requested.symbol,
                        exchange: attempt.exchange,
                        type: attempt.type,
                        limit: "50"
                    });

                    const response = await getJson(
                        "/tv/chart/search?" +
                        searchParams.toString()
                    );

                    const results = Array.isArray(
                        response.data?.tvSymbols
                    ) ? response.data.tvSymbols : [];

                    lastSearch = {
                        httpStatus: response.httpStatus,
                        query: requested.symbol,
                        exchange: attempt.exchange,
                        resultCount: results.length
                    };

                    const expected = normalize(
                        requested.symbol + "1"
                    );

                    selected = results.find(symbol => {
                        if (!symbol || !symbol.id) return false;

                        const name = normalize(
                            symbol.name || symbol.symbol
                        );

                        const validType =
                            !symbol.type ||
                            symbol.type === "stock";

                        const validExchange =
                            !attempt.exchange ||
                            !symbol.exchange ||
                            symbol.exchange === attempt.exchange;

                        return (
                            name === expected &&
                            validType &&
                            validExchange
                        );
                    }) || null;

                    if (selected) {
                        itemResult.search = {
                            ...lastSearch,
                            selected: {
                                id: selected.id,
                                ticker: selected.ticker || null,
                                name:
                                    selected.name ||
                                    selected.symbol || null,
                                full_name:
                                    selected.full_name ||
                                    selected.description || null,
                                type: selected.type || "stock",
                                exchange: selected.exchange || null
                            }
                        };
                        break;
                    }
                }

                if (!selected) {
                    itemResult.search = lastSearch;
                    itemResult.errors.push(
                        "Exact ordinary-stock ticker was not found."
                    );
                    result.symbols.push(itemResult);
                    await delay(200);
                    continue;
                }

                // قاعدهٔ شناسه از آزمایش موفق هر چهار نماد تأیید شده است.
                const rawId = String(selected.id);
                const canonicalId = rawId.endsWith("3")
                    ? rawId
                    : rawId + "3";

                const symbolParams = new URLSearchParams({
                    symbol: canonicalId
                });

                const symbolResponse = await getJson(
                    "/tv/chart/symbols?" +
                    symbolParams.toString()
                );

                const resolved =
                    symbolObject(symbolResponse.data);

                itemResult.resolvedSymbol = {
                    rawSearchId: rawId,
                    canonicalId: canonicalId,
                    httpStatus: symbolResponse.httpStatus,
                    id: resolved?.id || null,
                    ticker: resolved?.ticker || null,
                    name:
                        resolved?.name ||
                        resolved?.symbol || null,
                    full_name: resolved?.full_name || null,
                    type: resolved?.type || null
                };

                if (
                    symbolResponse.httpStatus !== 200 ||
                    !resolved ||
                    String(resolved.id || resolved.ticker)
                        !== canonicalId
                ) {
                    itemResult.errors.push(
                        "Canonical chart symbol ID was not confirmed."
                    );
                    result.symbols.push(itemResult);
                    await delay(200);
                    continue;
                }

                for (const resolution of resolutions) {
                    const historyParams = new URLSearchParams({
                        symbol: canonicalId,
                        resolution: resolution,
                        from: String(fromTime),
                        to: String(toTime),
                        type:
                            resolved.type ||
                            selected.type ||
                            "stock",
                        adjustmentType: "3",
                        countback: String(countback)
                    });

                    try {
                        const historyResponse = await getJson(
                            "/tv/chart/history?" +
                            historyParams.toString()
                        );

                        const data = historyResponse.data || {};

                        const arrays = {
                            time: data.t,
                            open: data.o,
                            high: data.h,
                            low: data.l,
                            close: data.c,
                            volume: data.v
                        };

                        const lengths = {};
                        for (
                            const [key, value]
                            of Object.entries(arrays)
                        ) {
                            lengths[key] = Array.isArray(value)
                                ? value.length
                                : 0;
                        }

                        const required = [
                            "time", "open", "high", "low", "close"
                        ];

                        const hasRequired = required.every(
                            key => Array.isArray(arrays[key])
                        );

                        const count = hasRequired
                            ? Math.min(
                                ...required.map(key => lengths[key])
                            )
                            : 0;

                        const status = data.s || data.status || null;

                        const success =
                            historyResponse.httpStatus === 200 &&
                            status === "ok" &&
                            count > 0;

                        itemResult.histories[resolution] = {
                            status: success ? "success" : "failed",
                            httpStatus: historyResponse.httpStatus,
                            apiStatus: status,
                            errorMessage:
                                data.errmsg || data.error || null,
                            arrayLengths: lengths,
                            count: count,
                            firstTime:
                                count ? arrays.time[0] : null,
                            lastTime:
                                count ? arrays.time[count - 1] : null,
                            series: success ? {
                                time: arrays.time.slice(0, count),
                                open: arrays.open.slice(0, count),
                                high: arrays.high.slice(0, count),
                                low: arrays.low.slice(0, count),
                                close: arrays.close.slice(0, count),
                                volume: Array.isArray(arrays.volume)
                                    ? arrays.volume.slice(0, count)
                                    : []
                            } : null,
                            bodyPreview: success
                                ? null
                                : historyResponse.bodyPreview
                        };
                    } catch (error) {
                        itemResult.histories[resolution] = {
                            status: "failed",
                            error: String(error)
                        };
                    }
                }

                const successes = Object.values(
                    itemResult.histories
                ).filter(h => h.status === "success").length;

                itemResult.status =
                    successes === resolutions.length
                        ? "success"
                        : successes > 0
                            ? "partial"
                            : "failed";

                if (successes === 0) {
                    itemResult.errors.push(
                        "No interval returned usable candles."
                    );
                }
            } catch (error) {
                itemResult.errors.push(String(error));
            }

            result.symbols.push(itemResult);

            // مکث کوچک بین نمادها
            await delay(200);
        }
    } catch (error) {
        result.errors.push(String(error));
    }

    done(result);
})().catch(error => done({
    fatalError: String(error)
}));
"""


def date_from_timestamp(value):
    if value is None:
        return None

    try:
        return datetime.fromtimestamp(
            int(value), timezone.utc
        ).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path, series, count):
    """Write one normalized candle per line."""
    required = ["time", "open", "high", "low", "close"]

    for key in required:
        if not isinstance(series.get(key), list):
            raise ValueError(f"Missing candle array: {key}")

    volume = series.get("volume")
    if not isinstance(volume, list):
        volume = []

    lines = []

    for index in range(count):
        timestamp = series["time"][index]
        candle = {
            "date": date_from_timestamp(timestamp),
            "time": timestamp,
            "open": series["open"][index],
            "high": series["high"][index],
            "low": series["low"][index],
            "close": series["close"][index],
            "volume": (
                volume[index]
                if index < len(volume)
                else None
            ),
        }

        lines.append(json.dumps(
            candle,
            ensure_ascii=False,
            separators=(",", ":")
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

    slug = safe_slug(symbol, source_item.get("id"))

    for resolution in RESOLUTIONS:
        history = (
            result.get("histories") or {}
        ).get(resolution) or {}

        status = history.get("status", "failed")
        interval = {
            "status": status,
            "http_status": history.get("httpStatus"),
            "api_status": history.get("apiStatus"),
            "count": history.get("count", 0),
            "first_date": date_from_timestamp(
                history.get("firstTime")
            ),
            "last_date": date_from_timestamp(
                history.get("lastTime")
            ),
            "error": (
                history.get("error") or
                history.get("errorMessage")
            ),
            "written": False,
        }

        if (
            status == "success" and
            isinstance(history.get("series"), dict)
        ):
            filename = interval_filename(slug, resolution)
            path = HISTORY_DIR / filename

            try:
                write_jsonl(
                    path,
                    history["series"],
                    int(history.get("count") or 0),
                )
                interval["file"] = f"data/history/{filename}"
                interval["written"] = True
            except Exception as error:
                interval["status"] = "failed_to_write"
                interval["error"] = str(error)

        summary["intervals"][resolution] = interval

    written_count = sum(
        1 for interval in summary["intervals"].values()
        if interval.get("written")
    )

    if written_count == len(RESOLUTIONS):
        summary["status"] = "success"
    elif written_count > 0:
        summary["status"] = "partial"
    else:
        summary["status"] = "failed"

    return summary


def main():
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    captured_at = utc_now()
    payload = {}
    selected_items = []
    collection_result = {}
    summaries = []
    fatal_error = None

    try:
        payload, market_items = load_marketmap()
        selected_items = select_symbols(market_items)

        print("Market map records:", len(market_items))
        print("Selected for this batch:", len(selected_items))
        print("Symbols:", ", ".join(
            item.get("symbol", "")
            for item in selected_items
        ))

        if not selected_items:
            raise ValueError(
                "No symbols selected from marketmap.json"
            )

        driver = make_driver()

        try:
            print("Opening chart:", CHART_URL)
            driver.get(CHART_URL)

            start_time = to_timestamp(START_DATE)
            end_time = int(
                datetime.now(timezone.utc).timestamp()
            ) + 86400

            requested = [
                {
                    "id": item.get("id"),
                    "symbol": item.get("symbol"),
                    "company": item.get("company"),
                    "close_price": item.get("close_price"),
                    "change_pct": item.get("change_pct"),
                    "area_raw": item.get("area_raw"),
                }
                for item in selected_items
            ]

            collection_result = driver.execute_async_script(
                BROWSER_COLLECTOR,
                requested,
                RESOLUTIONS,
                start_time,
                end_time,
                COUNTBACK,
            )
        finally:
            driver.quit()

        result_by_symbol = {
            normalize_symbol(
                (entry.get("marketmap") or {}).get("symbol")
            ): entry
            for entry in collection_result.get("symbols", [])
        }

        for source_item in selected_items:
            symbol = source_item.get("symbol", "")
            entry = result_by_symbol.get(
                normalize_symbol(symbol)
            )

            if entry is None:
                entry = {
                    "status": "failed",
                    "errors": [
                        "No result returned by browser collector."
                    ],
                    "histories": {},
                }

            summary = summarize_symbol(entry, source_item)
            summaries.append(summary)

            print(
                "\nSymbol:", symbol,
                "| Status:", summary["status"],
                "| Chart ID:",
                (summary.get("resolved_symbol") or {}).get(
                    "canonicalId", "-"
                )
            )

            for resolution in RESOLUTIONS:
                interval = summary["intervals"][resolution]

                print(
                    "  ", resolution,
                    "| Status:", interval["status"],
                    "| Candles:", interval.get("count", 0),
                    "| First:", interval.get("first_date") or "-",
                    "| Last:", interval.get("last_date") or "-",
                    "| File:", interval.get("file", "-"),
                    "| Error:", interval.get("error") or ""
                )

    except Exception as error:
        fatal_error = repr(error)
        print("COLLECTION ERROR:", fatal_error)

    finally:
        requested_count = len(selected_items)
        successful_count = sum(
            1 for item in summaries
            if item["status"] == "success"
        )
        partial_count = sum(
            1 for item in summaries
            if item["status"] == "partial"
        )
        failed_count = sum(
            1 for item in summaries
            if item["status"] == "failed"
        )

        manifest = {
            "source": "Nahayatnegar public chart interface",
            "captured_at_utc": captured_at,
            "marketmap_captured_at_utc":
                payload.get("captured_at_utc"),
            "marketmap_completeness_verified":
                payload.get("completeness_verified", False),
            "marketmap_count": payload.get("count"),
            "history_start_requested": START_DATE,
            "countback_requested": COUNTBACK,
            "resolutions_requested": RESOLUTIONS,
            "batch_size_requested": MAX_SYMBOLS,
            "requested_symbols": requested_count,
            "successful_symbols": successful_count,
            "partial_symbols": partial_count,
            "failed_symbols": failed_count,
            "status": (
                "error" if fatal_error else
                "success"
                if (
                    failed_count == 0 and
                    partial_count == 0 and
                    successful_count == requested_count
                )
                else "partial"
            ),
            "results": summaries,
            "collector_errors":
                collection_result.get("errors", []),
            "fatal_error": fatal_error,
            "notes": [
                "This is a 50-symbol load test, not a full-market scan.",
                "Market-map coverage is not independently verified.",
                "Only history with status=ok and non-empty candle arrays "
                "is counted as successful.",
                "Candle timestamps are stored as UTC timestamps and dates.",
                "Historical candles may be revised or adjusted by the source."
            ],
        }

        write_json(MANIFEST_FILE, manifest)

        print("\n" + "=" * 18 + " FINAL SUMMARY " + "=" * 18)
        print("Requested:", requested_count)
        print("Successful:", successful_count)
        print("Partial:", partial_count)
        print("Failed:", failed_count)
        print("Manifest:", MANIFEST_FILE.relative_to(ROOT))
        print("Run status:", manifest["status"])

    total_written = sum(
        1
        for symbol in summaries
        for interval in symbol.get("intervals", {}).values()
        if interval.get("written")
    )

    if fatal_error and total_written == 0:
        sys.exit(1)

    if summaries and total_written == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
