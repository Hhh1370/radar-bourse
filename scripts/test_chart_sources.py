import json
import os
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


CHART_URL = "https://www.nahayatnegar.com/tv/IRO1KSHJ0001"

# فقط دو نماد: حکشتی برای کنترل و فملی برای آزمایش
TEST_SYMBOLS = ["حکشتی", "فملی"]

# همان بازهٔ زمانی درخواست تاریخچه‌ای که قبلاً برای حکشتی موفق بود
FROM_TIME = 1755475200
TO_TIME = 1791849600
COUNTBACK = 301


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
    driver.set_script_timeout(180)
    return driver


BROWSER_TEST = r"""
const names = arguments[0];
const fromTime = arguments[1];
const toTime = arguments[2];
const countback = arguments[3];
const done = arguments[arguments.length - 1];

(async function () {
    const result = {
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

    for (const name of names) {
        const item = {
            requestedName: name,
            search: null,
            idTests: []
        };

        try {
            const searchParams = new URLSearchParams({
                query: name,
                exchange: "بورس",
                type: "stock",
                limit: "50"
            });

            const search = await getJson(
                "/tv/chart/search?" + searchParams.toString()
            );

            const matches = Array.isArray(search.data?.tvSymbols)
                ? search.data.tvSymbols
                : [];

            const normalizedName = String(name)
                .replace(/ي/g, "ی")
                .replace(/ك/g, "ک")
                .replace(/\u200c/g, "")
                .replace(/\s+/g, "")
                .trim();

            const expectedName = normalizedName + "1";

            const chosen = matches.find(s => {
                if (!s) return false;

                const symbolName = String(s.name || s.symbol || "")
                    .replace(/ي/g, "ی")
                    .replace(/ك/g, "ک")
                    .replace(/\u200c/g, "")
                    .replace(/\s+/g, "")
                    .trim();

                return (
                    symbolName === expectedName &&
                    (!s.type || s.type === "stock")
                );
            }) || null;

            item.search = {
                httpStatus: search.httpStatus,
                count: matches.length,
                chosen: chosen ? {
                    id: chosen.id || null,
                    ticker: chosen.ticker || null,
                    name: chosen.name || chosen.symbol || null,
                    full_name:
                        chosen.full_name || chosen.description || null,
                    type: chosen.type || null
                } : null,
                candidates: matches.slice(0, 8).map(s => ({
                    id: s.id || null,
                    name: s.name || s.symbol || null,
                    type: s.type || null
                })),
                bodyPreview: search.bodyPreview
            };

            if (!chosen || !chosen.id) {
                item.error = "Exact stock symbol not found.";
                result.symbols.push(item);
                continue;
            }

            const rawId = chosen.id;

            // آزمایش شناسهٔ جست‌وجو و حالت دارای پسوند 3
            const idsToTest = [...new Set([
                rawId,
                rawId.endsWith("3") ? rawId : rawId + "3"
            ])];

            for (const testId of idsToTest) {
                let symbolLookup = null;

                try {
                    const symbolParams = new URLSearchParams({
                        symbol: testId
                    });

                    symbolLookup = await getJson(
                        "/tv/chart/symbols?" +
                        symbolParams.toString()
                    );
                } catch (error) {
                    symbolLookup = {error: String(error)};
                }

                const historyParams = new URLSearchParams({
                    symbol: testId,
                    resolution: "1D",
                    from: String(fromTime),
                    to: String(toTime),
                    type: "stock",
                    adjustmentType: "3",
                    countback: String(countback)
                });

                const history = await getJson(
                    "/tv/chart/history?" +
                    historyParams.toString()
                );

                const data = history.data || {};
                const lengths = {};

                for (const [key, value] of Object.entries(data)) {
                    if (Array.isArray(value)) {
                        lengths[key] = value.length;
                    }
                }

                const candleCount = Math.max(
                    0,
                    ...Object.values(lengths)
                );

                const symbolData = symbolLookup?.data?.tvSymbol ||
                    symbolLookup?.data || null;

                item.idTests.push({
                    testId: testId,
                    isRawId: testId === rawId,
                    symbolLookup: {
                        httpStatus: symbolLookup?.httpStatus ?? null,
                        responseKeys:
                            symbolData &&
                            typeof symbolData === "object"
                                ? Object.keys(symbolData)
                                : [],
                        id: symbolData?.id || null,
                        ticker: symbolData?.ticker || null,
                        name:
                            symbolData?.name ||
                            symbolData?.symbol || null,
                        bodyPreview:
                            symbolLookup?.bodyPreview || null,
                        error: symbolLookup?.error || null
                    },
                    history: {
                        httpStatus: history.httpStatus,
                        apiStatus: data.s || data.status || null,
                        errorMessage:
                            data.errmsg || data.error || null,
                        arrayLengths: lengths,
                        candleCount: candleCount,
                        firstTime:
                            Array.isArray(data.t) && data.t.length
                                ? data.t[0]
                                : null,
                        lastTime:
                            Array.isArray(data.t) && data.t.length
                                ? data.t[data.t.length - 1]
                                : null,
                        lastClose:
                            Array.isArray(data.c) && data.c.length
                                ? data.c[data.c.length - 1]
                                : null,
                        bodyPreview:
                            candleCount ? null : history.bodyPreview
                    }
                });
            }
        } catch (error) {
            item.error = String(error);
        }

        result.symbols.push(item);
    }

    done(result);
})().catch(error => done({
    fatalError: String(error)
}));
"""


def main():
    driver = None

    print("=" * 18 + " SYMBOL ID DIAGNOSTIC " + "=" * 18)
    print("Chart URL:", CHART_URL)
    print("Symbols:", ", ".join(TEST_SYMBOLS))
    print("Testing raw ID against ID + 3")

    try:
        driver = make_driver()
        driver.get(CHART_URL)

        result = driver.execute_async_script(
            BROWSER_TEST,
            TEST_SYMBOLS,
            FROM_TIME,
            TO_TIME,
            COUNTBACK
        )

        print("\n" + "=" * 18 + " QUICK SUMMARY " + "=" * 18)

        for symbol in result.get("symbols", []):
            search = symbol.get("search") or {}
            chosen = search.get("chosen") or {}

            print(
                "\nSymbol:", symbol.get("requestedName"),
                "| Search HTTP:", search.get("httpStatus"),
                "| Search matches:", search.get("count"),
                "| Selected:", chosen.get("name") or "-",
                "| Raw ID:", chosen.get("id") or "-"
            )

            for test in symbol.get("idTests", []):
                history = test.get("history") or {}
                lookup = test.get("symbolLookup") or {}

                print(
                    "  ID:", test.get("testId"),
                    "| Raw ID:", test.get("isRawId"),
                    "| Symbol endpoint:", lookup.get("httpStatus"),
                    "| Resolved ID:", lookup.get("id") or "-",
                    "| History HTTP:", history.get("httpStatus"),
                    "| API:", history.get("apiStatus"),
                    "| Candles:", history.get("candleCount", 0),
                    "| Last close:", history.get("lastClose", "-")
                )

            if symbol.get("error"):
                print("  Error:", symbol["error"])

        print("\n" + "=" * 18 + " FULL RESULTS " + "=" * 18)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    except Exception as error:
        print("TEST FAILED:", repr(error))
        raise

    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    main()
