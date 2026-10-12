import json
import os
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


CHART_URL = "https://www.nahayatnegar.com/tv/IRO1KSHJ0001"

TEST_SYMBOLS = [
    {"name": "فملی", "queries": ["فملی", "IRO1MESC0001"]},
    {"name": "کچاد", "queries": ["کچاد"]},
    {"name": "فولاد", "queries": ["فولاد"]},
    {"name": "حکشتی", "queries": ["حکشتی", "IRO1KSHJ00013"]},
]

EXCHANGES = ["بورس", ""]
SYMBOL_TYPES = ["stock", ""]


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
const testSymbols = arguments[0];
const exchanges = arguments[1];
const types = arguments[2];
const done = arguments[arguments.length - 1];

(async function () {
    const output = {
        pageUrl: location.href,
        config: null,
        tests: [],
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
                bodyPreview: text.slice(0, 1500)
            };
        }

        return {
            httpStatus: response.status,
            data: data
        };
    }

    function summarize(data) {
        if (Array.isArray(data)) {
            return {
                responseType: "array",
                count: data.length,
                items: data.slice(0, 10).map(item => ({
                    symbol: item.symbol || item.ticker || "",
                    full_name: item.full_name || item.description || "",
                    exchange: item.exchange || "",
                    type: item.type || "",
                    raw: item
                }))
            };
        }

        if (data && typeof data === "object") {
            return {
                responseType: "object",
                keys: Object.keys(data),
                items: data.symbols || data.results || data.data || null,
                raw: data
            };
        }

        return {
            responseType: typeof data,
            raw: data
        };
    }

    try {
        const config = await requestJson("/tv/chart/config");
        output.config = {
            httpStatus: config.httpStatus,
            data: config.data || null,
            parseError: config.parseError || null
        };

        for (const testSymbol of testSymbols) {
            for (const query of testSymbol.queries) {
                for (const exchange of exchanges) {
                    for (const type of types) {
                        const params = new URLSearchParams({
                            query: query,
                            exchange: exchange,
                            type: type,
                            limit: "10"
                        });

                        const path = "/tv/chart/search?" +
                            params.toString();

                        try {
                            const response = await requestJson(path);

                            output.tests.push({
                                wantedName: testSymbol.name,
                                query: query,
                                exchange: exchange,
                                type: type,
                                endpoint: path,
                                httpStatus: response.httpStatus,
                                summary: summarize(response.data),
                                parseError: response.parseError || null,
                                bodyPreview:
                                    response.bodyPreview || null
                            });
                        } catch (error) {
                            output.tests.push({
                                wantedName: testSymbol.name,
                                query: query,
                                exchange: exchange,
                                type: type,
                                endpoint: path,
                                error: String(error)
                            });
                        }
                    }
                }
            }
        }
    } catch (error) {
        output.errors.push(String(error));
    }

    done(output);
})().catch(error => done({
    fatalError: String(error)
}));
"""


def main():
    driver = None

    print("=" * 18 + " SYMBOL SEARCH TEST " + "=" * 18)
    print("Chart URL:", CHART_URL)
    print("Symbols:", ", ".join(x["name"] for x in TEST_SYMBOLS))

    try:
        driver = make_driver()
        driver.get(CHART_URL)

        result = driver.execute_async_script(
            BROWSER_TEST,
            TEST_SYMBOLS,
            EXCHANGES,
            SYMBOL_TYPES
        )

        print("\n" + "=" * 18 + " FULL SEARCH RESULTS " + "=" * 18)
        print(json.dumps(result, ensure_ascii=False, indent=2))

        print("\n" + "=" * 18 + " QUICK SUMMARY " + "=" * 18)

        config = result.get("config") or {}
        print("Config HTTP:", config.get("httpStatus"))
        print(
            "Search supported:",
            (config.get("data") or {}).get("supports_search")
        )

        tests = result.get("tests", [])
        successful = 0
        results_found = 0

        for test in tests:
            status = test.get("httpStatus")
            summary = test.get("summary") or {}

            if status == 200:
                successful += 1

            if summary.get("count", 0) > 0:
                results_found += 1

            items = summary.get("items")
            if isinstance(items, list):
                item_names = [
                    item.get("symbol", "")
                    for item in items[:5]
                    if isinstance(item, dict)
                ]
            else:
                item_names = []

            print(
                "Wanted:", test.get("wantedName"),
                "| Query:", test.get("query"),
                "| Exchange:", repr(test.get("exchange")),
                "| Type:", repr(test.get("type")),
                "| HTTP:", status,
                "| Count:", summary.get("count", "-"),
                "| Symbols:", ", ".join(item_names),
                "| Error:", test.get("error", "")
            )

        print("\nTotal search requests:", len(tests))
        print("HTTP 200 responses:", successful)
        print("Tests returning array results:", results_found)

        if result.get("errors"):
            print("Errors:", json.dumps(
                result["errors"],
                ensure_ascii=False
            ))

        if result.get("fatalError"):
            print("Fatal:", result["fatalError"])

    except Exception as error:
        print("TEST FAILED:", repr(error))
        raise

    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    main()
