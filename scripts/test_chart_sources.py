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

    async function getJson(path) {
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
            .replace(/\s+/g, "")
            .trim();
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
        const lengths = {};

        if (data && typeof data === "object") {
            for (const [key, value] of Object.entries(data)) {
                if (Array.isArray(value)) {
                    lengths[key] = value.length;
                }
            }
        }

        return lengths;
    }

    function sampleCandles(data) {
        const t = data.t || [];
        const o = data.o || [];
        const h = data.h || [];
        const l = data.l || [];
        const c = data.c || [];
        const v = data.v || [];

        const count = Math.max(
            t.length,
            o.length,
            h.length,
            l.length,
            c.length,
            v.length
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

        if (count === 0) {
            return {count: 0, first: null, last: null};
        }

        return {
            count: count,
            first: candle(0),
            last: candle(count - 1)
        };
    }

    try {
        const configResponse = await getJson("/tv/chart/config");

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
                chosenSearchResult: null,
                canonicalId: null,
                symbolLookup: null,
                history: [],
                errors: []
            };

            try {
                // مرحلهٔ اول: جست‌وجوی دقیق نماد
                const searchParams = new URLSearchParams({
                    query: requestedName,
                    exchange: "بورس",
                    type: "stock",
                    limit: "50"
                });

                const searchResponse = await getJson(
                    "/tv/chart/search?" + searchParams.toString()
                );

                item.searchHttpStatus = searchResponse.httpStatus;

                const matches = Array.isArray(
                    searchResponse.data?.tvSymbols
                ) ? searchResponse.data.tvSymbols : [];

                const targetName = normalize(requestedName + "1");

                const selected = matches.find(symbol => {
                    if (!symbol) return false;

                    const name = normalize(
                        symbol.name || symbol.symbol || ""
                    );

                    return (
                        name === targetName &&
                        (
                            !symbol.type ||
                            symbol.type === "stock"
                        ) &&
                        (
                            !symbol.exchange ||
                            symbol.exchange === "بورس"
                        )
                    );
                });

                if (!selected || !selected.id) {
                    item.errors.push(
                        "No exact ordinary-stock match found."
                    );

                    item.searchCandidates = matches.slice(0, 10).map(
                        symbol => ({
                            id: symbol.id || null,
                            name: symbol.name || symbol.symbol || null,
                            type: symbol.type || null,
                            exchange: symbol.exchange || null
                        })
                    );

                    output.symbols.push(item);
                    continue;
                }

                item.chosenSearchResult = {
                    id: selected.id,
                    ticker: selected.ticker || null,
                    name: selected.name || selected.symbol || null,
                    full_name:
                        selected.full_name || selected.description || null,
                    type: selected.type || "stock",
                    exchange: selected.exchange || null
                };

                // مرحلهٔ دوم: اصلاح شناسه بر اساس آزمایش تأییدشده
                const rawId = String(selected.id);
                const canonicalId = rawId.endsWith("3")
                    ? rawId
                    : rawId + "3";

                item.canonicalId = canonicalId;

                // مرحلهٔ سوم: تأیید شناسه با endpoint خود سایت
                const symbolParams = new URLSearchParams({
                    symbol: canonicalId
                });

                const symbolResponse = await getJson(
                    "/tv/chart/symbols?" + symbolParams.toString()
                );

                const resolved = getSymbolObject(symbolResponse.data);

                item.symbolLookup = {
                    httpStatus: symbolResponse.httpStatus,
                    resolvedId: resolved?.id || null,
                    resolvedTicker: resolved?.ticker || null,
                    resolvedName:
                        resolved?.name || resolved?.symbol || null,
                    full_name: resolved?.full_name || null,
                    type: resolved?.type || null,
                    bodyPreview: resolved ? null :
                        symbolResponse.bodyPreview
                };

                // اگر API شناسه را تأیید نکرد، تاریخچه را حدس نمی‌زنیم.
                if (
                    symbolResponse.httpStatus !== 200 ||
                    !resolved ||
                    String(resolved.id || resolved.ticker) !== canonicalId
                ) {
                    item.errors.push(
                        "Canonical ID was not confirmed by symbol endpoint."
                    );
                    output.symbols.push(item);
                    continue;
                }

                // مرحلهٔ چهارم: دریافت تاریخچه در هر بازه
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

                    const historyResponse = await getJson(
                        "/tv/chart/history?" + params.toString()
                    );

                    const data = historyResponse.data || {};
                    const sample = sampleCandles(data);

                    item.history.push({
                        resolution: resolution,
                        httpStatus: historyResponse.httpStatus,
                        apiStatus: data.s || data.status || null,
                        errorMessage: data.errmsg || data.error || null,
                        candleCount: sample.count,
                        arrayLengths: getArrayLengths(data),
                        firstCandle: sample.first,
                        lastCandle: sample.last,
                        bodyPreview: sample.count ? null :
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
            int(timestamp),
            timezone.utc
        ).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OverflowError, OSError):
        return str(timestamp)


def main():
    start_time = utc_timestamp(START_DATE)
    end_time = int(datetime.now(timezone.utc).timestamp()) + 86400

    print("=" * 20 + " TEST SETTINGS " + "=" * 20)
    print("Chart URL:", CHART_URL)
    print("Symbols:", ", ".join(TEST_SYMBOLS))
    print("History start:", START_DATE)
    print("Resolutions:", ", ".join(RESOLUTIONS))

    driver = None

    try:
        driver = make_driver()
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

        total_candles = 0
        successful_histories = 0

        for item in result.get("symbols", []):
            chosen = item.get("chosenSearchResult") or {}
            lookup = item.get("symbolLookup") or {}

            print(
                "\nSymbol:", item.get("requestedName"),
                "| Search HTTP:", item.get("searchHttpStatus"),
                "| Name:", chosen.get("name", "-"),
                "| Search ID:", chosen.get("id", "-"),
                "| Canonical ID:", item.get("canonicalId", "-"),
                "| Verified ID:", lookup.get("resolvedId", "-")
            )

            for history in item.get("history", []):
                first = history.get("firstCandle") or {}
                last = history.get("lastCandle") or {}
                candles = history.get("candleCount", 0)

                if history.get("apiStatus") == "ok" and candles > 0:
                    successful_histories += 1
                    total_candles += candles

                print(
                    "  Resolution:", history.get("resolution"),
                    "| HTTP:", history.get("httpStatus"),
                    "| API:", history.get("apiStatus"),
                    "| Candles:", candles,
                    "| First:", format_date(first.get("time")),
                    "| Last:", format_date(last.get("time")),
                    "| Latest close:", last.get("close", "-"),
                    "| Error:", history.get("errorMessage") or ""
                )

            for error in item.get("errors", []):
                print("  Error:", error)

        print("\nSuccessful history requests:", successful_histories)
        print("Total candles across successful requests:", total_candles)

        if result.get("errors"):
            print("General errors:", json.dumps(
                result["errors"], ensure_ascii=False
            ))

        if result.get("fatalError"):
            print("Fatal error:", result["fatalError"])

        print("\n" + "=" * 20 + " FULL RESULTS " + "=" * 20)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    except Exception as error:
        print("TEST FAILED:", repr(error))
        raise

    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    main()
