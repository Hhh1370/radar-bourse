import json
import os
from datetime import datetime, timezone
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


CHART_URL = "https://www.nahayatnegar.com/tv/IRO1KSHJ0001"

TEST_SYMBOLS = ["فملی", "کچاد", "فولاد", "حکشتی"]
RESOLUTIONS = ["1D", "W", "M"]
START_DATE = "2018-01-01"
COUNTBACK = 5000


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
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(60)
    driver.set_script_timeout(240)
    return driver


def utc_timestamp(date_text):
    value = datetime.strptime(date_text, "%Y-%m-%d")
    return int(value.replace(tzinfo=timezone.utc).timestamp())


BROWSER_TEST = r"""
const requestedSymbols = arguments[0];
const resolutions = arguments[1];
const fromTime = arguments[2];
const toTime = arguments[3];
const countback = arguments[4];
const done = arguments[arguments.length - 1];

(async function () {
    const output = {
        config: null,
        symbols: [],
        errors: []
    };

    async function requestJson(path) {
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
            bodyPreview: text.slice(0, 500)
        };
    }

    function normalize(value) {
        return String(value || "")
            .replace(/ي/g, "ی")
            .replace(/ك/g, "ک")
            .replace(/\u200c/g, "")
            .replace(/\s+/g, "")
            .trim()
            .toLowerCase();
    }

    function getSymbolObject(data) {
        if (!data || typeof data !== "object") return null;

        if (data.tvSymbol && typeof data.tvSymbol === "object") {
            return data.tvSymbol;
        }

        if (data.id || data.ticker || data.full_name) {
            return data;
        }

        return null;
    }

    function getArrayLengths(data) {
        const output = {};

        if (data && typeof data === "object") {
            for (const [key, value] of Object.entries(data)) {
                if (Array.isArray(value)) {
                    output[key] = value.length;
                }
            }
        }

        return output;
    }

    function sampleCandles(data) {
        const t = data.t || [];
        const o = data.o || [];
        const h = data.h || [];
        const l = data.l || [];
        const c = data.c || [];
        const v = data.v || [];

        const count = Math.max(
            t.length, o.length, h.length,
            l.length, c.length, v.length
        );

        function candle(i) {
            return {
                time: t[i] ?? null,
                open: o[i] ?? null,
                high: h[i] ?? null,
                low: l[i] ?? null,
                close: c[i] ?? null,
                volume: v[i] ?? null
            };
        }

        if (!count) {
            return {count: 0, first: null, last: null};
        }

        return {
            count: count,
            first: candle(0),
            last: candle(count - 1)
        };
    }

    try {
        const configResponse =
            await requestJson("/tv/chart/config");

        output.config = {
            httpStatus: configResponse.httpStatus,
            supportsSearch:
                configResponse.data?.supports_search ?? null,
            supportedResolutions:
                configResponse.data?.supported_resolutions || []
        };

        for (const requestedName of requestedSymbols) {
            const item = {
                requestedName: requestedName,
                searchHttpStatus: null,
                searchResults: [],
                chosenSearchResult: null,
                symbolResolutionAttempts: [],
                resolvedSymbol: null,
                history: [],
                errors: []
            };

            try {
                // 1. جست‌وجوی نماد در بازار بورس
                const searchParams = new URLSearchParams({
                    query: requestedName,
                    exchange: "بورس",
                    type: "stock",
                    limit: "50"
                });

                const searchResponse = await requestJson(
                    "/tv/chart/search?" + searchParams.toString()
                );

                item.searchHttpStatus = searchResponse.httpStatus;

                const results = Array.isArray(
                    searchResponse.data?.tvSymbols
                ) ? searchResponse.data.tvSymbols : [];

                item.searchResults = results.slice(0, 15).map(s => ({
                    id: s.id || null,
                    ticker: s.ticker || null,
                    name: s.name || s.symbol || null,
                    full_name: s.full_name || s.description || null,
                    type: s.type || null,
                    exchange: s.exchange || null
                }));

                // تطبیق دقیق: مثلاً فولاد1، نه فولاد تربت1
                const wanted = normalize(requestedName);
                const expectedName = normalize(requestedName + "1");

                const stockResults = results.filter(s =>
                    s &&
                    (s.type === "stock" || !s.type) &&
                    (
                        !s.exchange ||
                        s.exchange === "بورس"
                    )
                );

                const chosen =
                    stockResults.find(s =>
                        normalize(s.name) === expectedName ||
                        normalize(s.symbol) === expectedName
                    ) ||
                    stockResults.find(s =>
                        normalize(s.name) === wanted ||
                        normalize(s.symbol) === wanted
                    ) ||
                    null;

                if (!chosen) {
                    item.errors.push(
                        "Exact symbol match not found; history was not requested."
                    );
                    output.symbols.push(item);
                    continue;
                }

                item.chosenSearchResult = {
                    id: chosen.id || null,
                    ticker: chosen.ticker || null,
                    name: chosen.name || chosen.symbol || null,
                    full_name: chosen.full_name ||
                        chosen.description || null,
                    type: chosen.type || "stock",
                    exchange: chosen.exchange || null
                };

                // 2. شناسهٔ جست‌وجو را از endpoint نماد عبور می‌دهیم.
                const candidates = [
                    chosen.ticker,
                    chosen.id,
                    chosen.name,
                    chosen.symbol
                ].filter(Boolean);

                const uniqueCandidates = [...new Set(candidates)];
                let resolved = null;

                for (const candidate of uniqueCandidates) {
                    const symbolParams = new URLSearchParams({
                        symbol: candidate
                    });

                    const symbolResponse = await requestJson(
                        "/tv/chart/symbols?" +
                        symbolParams.toString()
                    );

                    const symbolObject =
                        getSymbolObject(symbolResponse.data);

                    const attempt = {
                        requestedValue: candidate,
                        httpStatus: symbolResponse.httpStatus,
                        resolvedId: symbolObject?.id || null,
                        resolvedTicker: symbolObject?.ticker || null,
                        resolvedName:
                            symbolObject?.name ||
                            symbolObject?.symbol || null,
                        full_name: symbolObject?.full_name || null,
                        type: symbolObject?.type || null,
                        responseKeys:
                            symbolResponse.data &&
                            typeof symbolResponse.data === "object"
                                ? Object.keys(symbolResponse.data)
                                : [],
                        bodyPreview: symbolObject ? null :
                            symbolResponse.bodyPreview
                    };

                    item.symbolResolutionAttempts.push(attempt);

                    if (
                        symbolResponse.httpStatus === 200 &&
                        symbolObject &&
                        (symbolObject.id || symbolObject.ticker)
                    ) {
                        resolved = symbolObject;
                        break;
                    }
                }

                if (!resolved) {
                    item.errors.push(
                        "Could not resolve a canonical chart symbol ID."
                    );
                    output.symbols.push(item);
                    continue;
                }

                const canonicalId = resolved.id || resolved.ticker;

                item.resolvedSymbol = {
                    id: resolved.id || null,
                    ticker: resolved.ticker || null,
                    name: resolved.name || resolved.symbol || null,
                    full_name: resolved.full_name || null,
                    type: resolved.type || "stock",
                    exchange: resolved.exchange || null
                };

                // 3. تاریخچه فقط با شناسهٔ تأییدشده درخواست می‌شود.
                for (const resolution of resolutions) {
                    const historyParams = new URLSearchParams({
                        symbol: canonicalId,
                        resolution: resolution,
                        from: String(fromTime),
                        to: String(toTime),
                        type: resolved.type || chosen.type || "stock",
                        adjustmentType: "3",
                        countback: String(countback)
                    });

                    const historyResponse = await requestJson(
                        "/tv/chart/history?" +
                        historyParams.toString()
                    );

                    const data = historyResponse.data || {};
                    const samples = sampleCandles(data);

                    item.history.push({
                        resolution: resolution,
                        requestedId: canonicalId,
                        httpStatus: historyResponse.httpStatus,
                        apiStatus: data.s || data.status || null,
                        errorMessage: data.errmsg || data.error || null,
                        arrayLengths: getArrayLengths(data),
                        samples: samples,
                        responseKeys: Object.keys(data),
                        bodyPreview: samples.count ? null :
                            historyResponse.bodyPreview
                    });
                }
            } catch (error) {
                item.errors.push(String(error));
            }

            output.symbols.push(item);
        }
    } catch (error) {
        output.errors.push(String(error));
    }

    done(output);
})().catch(error => done({
    fatalError: String(error)
}));
"""


def format_date(timestamp):
    if timestamp is None:
        return "-"

    try:
        return datetime.fromtimestamp(
            int(timestamp), timezone.utc
        ).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OverflowError, OSError):
        return str(timestamp)


def main():
    start_time = utc_timestamp(START_DATE)
    end_time = int(datetime.now(timezone.utc).timestamp()) + 86400

    print("=" * 20 + " TEST SETTINGS " + "=" * 20)
    print("Chart URL:", CHART_URL)
    print("Symbols:", ", ".join(TEST_SYMBOLS))
    print("Start date:", START_DATE)
    print("Resolutions:", ", ".join(RESOLUTIONS))

    driver = None

    try:
        options_ready = False
        driver = make_driver()
        options_ready = True

        driver.get(CHART_URL)

        result = driver.execute_async_script(
            BROWSER_TEST,
            TEST_SYMBOLS,
            RESOLUTIONS,
            start_time,
            end_time,
            COUNTBACK
        )

        print("\n" + "=" * 20 + " QUICK SUMMARY " + "=" * 20)

        config = result.get("config") or {}
        print("Config HTTP:", config.get("httpStatus"))
        print("Search supported:", config.get("supportsSearch"))

        for item in result.get("symbols", []):
            chosen = item.get("chosenSearchResult") or {}
            resolved = item.get("resolvedSymbol") or {}

            print(
                "\nSymbol:", item.get("requestedName"),
                "| Search HTTP:", item.get("searchHttpStatus"),
                "| Chosen:", chosen.get("name") or "-",
                "| Search ID:", chosen.get("id") or "-",
                "| Canonical ID:", resolved.get("id") or "-",
                "| Resolved name:", resolved.get("name") or "-"
            )

            for history in item.get("history", []):
                samples = history.get("samples") or {}
                first = samples.get("first") or {}
                last = samples.get("last") or {}

                print(
                    "  Resolution:", history.get("resolution"),
                    "| HTTP:", history.get("httpStatus"),
                    "| API:", history.get("apiStatus"),
                    "| Candles:", samples.get("count", 0),
                    "| First:", format_date(first.get("time")),
                    "| Last:", format_date(last.get("time")),
                    "| Latest close:", last.get("close", "-"),
                    "| Error:",
                    history.get("errorMessage") or ""
                )

            for attempt in item.get("symbolResolutionAttempts", []):
                print(
                    "  Symbol lookup:",
                    attempt.get("requestedValue"),
                    "| HTTP:", attempt.get("httpStatus"),
                    "| ID:", attempt.get("resolvedId") or "-",
                    "| Name:", attempt.get("resolvedName") or "-"
                )

            for error in item.get("errors", []):
                print("  Error:", error)

        print("\n" + "=" * 20 + " FULL RESULTS " + "=" * 20)
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if result.get("errors"):
            print("General errors:", json.dumps(
                result["errors"], ensure_ascii=False
            ))

        if result.get("fatalError"):
            print("Fatal error:", result["fatalError"])

    except Exception as error:
        print("TEST FAILED:", repr(error))
        raise

    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    main()
