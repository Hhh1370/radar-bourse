import json
import os
from datetime import datetime, timezone
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


CHART_URL = "https://www.nahayatnegar.com/tv/IRO1KSHJ0001"

# آزمایش نمادهای اصلی بورس ایران
TEST_SYMBOLS = ["فملی", "کچاد", "فولاد", "حکشتی"]

# داده‌های بلندمدت از سال ۲۰۱۸
START_DATE = "2018-01-01"
RESOLUTIONS = ["1D", "W", "M"]
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
    value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


BROWSER_TEST = r"""
const requestedSymbols = arguments[0];
const resolutions = arguments[1];
const fromTime = arguments[2];
const toTime = arguments[3];
const countback = arguments[4];
const done = arguments[arguments.length - 1];

(async function () {
    const result = {
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

        let data;
        try {
            data = JSON.parse(text);
        } catch (error) {
            return {
                httpStatus: response.status,
                parseError: String(error),
                bodyPreview: text.slice(0, 500)
            };
        }

        return {
            httpStatus: response.status,
            data: data
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

    function makeSample(data) {
        const times = data.t || [];
        const opens = data.o || [];
        const highs = data.h || [];
        const lows = data.l || [];
        const closes = data.c || [];
        const volumes = data.v || [];

        const count = Math.max(
            times.length,
            opens.length,
            highs.length,
            lows.length,
            closes.length,
            volumes.length
        );

        if (!count) {
            return {
                count: 0,
                first: null,
                last: null
            };
        }

        function candle(i) {
            return {
                time: times[i] ?? null,
                open: opens[i] ?? null,
                high: highs[i] ?? null,
                low: lows[i] ?? null,
                close: closes[i] ?? null,
                volume: volumes[i] ?? null
            };
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

        result.config = {
            httpStatus: configResponse.httpStatus,
            supportsSearch:
                configResponse.data?.supports_search ?? null,
            supportedResolutions:
                configResponse.data?.supported_resolutions || [],
            error: configResponse.parseError || null
        };

        for (const requestedName of requestedSymbols) {
            const symbolResult = {
                requestedName: requestedName,
                searchHttpStatus: null,
                searchCount: 0,
                chosenSymbol: null,
                history: [],
                errors: []
            };

            try {
                // شناسه را از API جست‌وجوی خود چارت پیدا می‌کنیم.
                const params = new URLSearchParams({
                    query: requestedName,
                    exchange: "",
                    type: "stock",
                    limit: "10"
                });

                const searchResponse = await requestJson(
                    "/tv/chart/search?" + params.toString()
                );

                symbolResult.searchHttpStatus =
                    searchResponse.httpStatus;

                if (!searchResponse.data) {
                    throw new Error(
                        "Search response has no JSON data: " +
                        JSON.stringify(searchResponse).slice(0, 500)
                    );
                }

                const matches =
                    Array.isArray(searchResponse.data.tvSymbols)
                    ? searchResponse.data.tvSymbols
                    : [];

                symbolResult.searchCount = matches.length;

                // فقط سهام عادی؛ حق‌تقدم‌ها یا ابزارهای دیگر انتخاب نشوند.
                const stocks = matches.filter(item =>
                    item &&
                    item.id &&
                    (
                        item.type === "stock" ||
                        item.type === null ||
                        item.type === undefined
                    )
                );

                const wanted = normalize(requestedName);

                // اول نمادی که نامش با نماد درخواستی شروع می‌شود.
                // در صورت نبود، نخستین نتیجهٔ معتبر جست‌وجو.
                const chosen =
                    stocks.find(item =>
                        normalize(item.name).startsWith(wanted)
                    ) ||
                    stocks[0] ||
                    null;

                if (!chosen) {
                    symbolResult.errors.push(
                        "No usable stock symbol found. Raw keys: " +
                        Object.keys(searchResponse.data).join(", ")
                    );

                    symbolResult.searchMatches = matches.map(item => ({
                        id: item.id || null,
                        name: item.name || null,
                        symbol: item.symbol || null,
                        type: item.type || null,
                        exchange: item.exchange || null
                    }));

                    result.symbols.push(symbolResult);
                    continue;
                }

                const canonicalSymbol =
                    chosen.id || chosen.ticker;

                symbolResult.chosenSymbol = {
                    id: chosen.id || null,
                    ticker: chosen.ticker || null,
                    name: chosen.name || chosen.symbol || null,
                    full_name: chosen.full_name ||
                        chosen.description || null,
                    exchange: chosen.exchange || null,
                    type: chosen.type || "stock"
                };

                // تاریخچه را برای هر سه بازه دریافت می‌کنیم.
                for (const resolution of resolutions) {
                    const historyParams = new URLSearchParams({
                        symbol: canonicalSymbol,
                        resolution: resolution,
                        from: String(fromTime),
                        to: String(toTime),
                        type: chosen.type || "stock",
                        adjustmentType: "3",
                        countback: String(countback)
                    });

                    try {
                        const historyResponse = await requestJson(
                            "/tv/chart/history?" +
                            historyParams.toString()
                        );

                        const data = historyResponse.data || {};

                        symbolResult.history.push({
                            resolution: resolution,
                            httpStatus: historyResponse.httpStatus,
                            apiStatus: data.s || data.status || null,
                            errorMessage:
                                data.errmsg || data.error || null,
                            arrayLengths: getArrayLengths(data),
                            sample: makeSample(data),
                            rawKeys: Object.keys(data),
                            parseError:
                                historyResponse.parseError || null
                        });
                    } catch (error) {
                        symbolResult.history.push({
                            resolution: resolution,
                            error: String(error)
                        });
                    }
                }
            } catch (error) {
                symbolResult.errors.push(String(error));
            }

            result.symbols.push(symbolResult);
        }
    } catch (error) {
        result.errors.push(String(error));
    }

    done(result);
})().catch(error => done({
    fatalError: String(error)
}));
"""


def format_timestamp(value):
    if value is None:
        return "-"

    try:
        return datetime.fromtimestamp(
            int(value),
            timezone.utc
        ).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OverflowError, OSError):
        return str(value)


def main():
    start_time = utc_timestamp(START_DATE)
    end_time = int(datetime.now(timezone.utc).timestamp()) + 86400

    print("=" * 20 + " TEST SETTINGS " + "=" * 20)
    print("Chart URL:", CHART_URL)
    print("Requested symbols:", ", ".join(TEST_SYMBOLS))
    print("History starts:", START_DATE)
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

        for item in result.get("symbols", []):
            chosen = item.get("chosenSymbol") or {}

            print(
                "\nSymbol:", item.get("requestedName"),
                "| Search HTTP:", item.get("searchHttpStatus"),
                "| Matches:", item.get("searchCount"),
                "| Chosen:", chosen.get("name") or "-",
                "| ID:", chosen.get("id") or "-"
            )

            for history in item.get("history", []):
                sample = history.get("sample") or {}
                first = sample.get("first") or {}
                last = sample.get("last") or {}

                print(
                    "  Resolution:", history.get("resolution"),
                    "| HTTP:", history.get("httpStatus"),
                    "| API:", history.get("apiStatus"),
                    "| Candles:", sample.get("count", 0),
                    "| First:", format_timestamp(first.get("time")),
                    "| Last:", format_timestamp(last.get("time")),
                    "| Latest close:", last.get("close", "-"),
                    "| Error:",
                    history.get("errorMessage") or
                    history.get("error") or ""
                )

            for error in item.get("errors", []):
                print("  Symbol error:", error)

        if result.get("errors"):
            print(
                "\nGeneral errors:",
                json.dumps(result["errors"], ensure_ascii=False)
            )

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
