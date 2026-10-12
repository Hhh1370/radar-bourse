import json
import os
import time
from urllib.parse import urlencode

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait


BASE_URL = "https://rahavard365.com"
PAGE_URL = f"{BASE_URL}/marketmap"
API_PATH = "/api/v2/marketmap/data"


def extract_stocks(node, results=None):
    if results is None:
        results = {}

    if isinstance(node, list):
        for item in node:
            extract_stocks(item, results)
        return results

    if not isinstance(node, dict):
        return results

    children = node.get("children")

    if isinstance(children, list):
        for child in children:
            extract_stocks(child, results)
        return results

    if (
        node.get("id") is not None
        and node.get("name")
        and node.get("fullname")
        and (
            "real_close_price" in node
            or "value" in node
        )
    ):
        results[str(node["id"])] = {
            "id": str(node["id"]),
            "symbol": str(node["name"]).strip(),
            "company": str(node["fullname"]).strip(),
        }
        return results

    for key, value in node.items():
        if key != "children":
            extract_stocks(value, results)

    return results


def main():
    options = Options()
    options.binary_location = os.environ["CHROME_BIN"]
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=fa-IR")

    driver = webdriver.Chrome(
        service=Service(os.environ["CHROMEDRIVER"]),
        options=options,
    )

    try:
        driver.set_page_load_timeout(45)
        driver.set_script_timeout(60)

        print("Opening Rahavard...")
        driver.get(PAGE_URL)

        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script(
                "return document.readyState"
            ) == "complete"
        )
        time.sleep(3)

        tests = [
            {"label": "ALL_MARKETS", "market_id": None},
            {"label": "BORSE", "market_id": "1"},
            {"label": "FARABORSE", "market_id": "2"},
        ]

        results = driver.execute_async_script("""
            const base = arguments[0];
            const apiPath = arguments[1];
            const tests = arguments[2];
            const done = arguments[arguments.length - 1];

            (async () => {
                const output = [];

                for (const test of tests) {
                    const params = new URLSearchParams({
                        asset_id: "1",
                        coloring_type_id: "1",
                        size_type_id: "1",
                        category_id: "1"
                    });

                    if (test.market_id !== null) {
                        params.set("market_id", test.market_id);
                    }

                    const url = base + apiPath + "?" + params.toString();

                    try {
                        const response = await fetch(url, {
                            method: "GET",
                            credentials: "include",
                            cache: "no-store",
                            headers: {"Accept": "application/json"}
                        });

                        const body = await response.text();
                        let data = null;
                        let parseError = null;

                        try {
                            data = JSON.parse(body);
                        } catch (error) {
                            parseError = String(error);
                        }

                        output.push({
                            label: test.label,
                            url: url,
                            status: response.status,
                            bytes: new TextEncoder().encode(body).length,
                            data: data,
                            parseError: parseError
                        });
                    } catch (error) {
                        output.push({
                            label: test.label,
                            url: url,
                            error: String(error)
                        });
                    }
                }

                done(output);
            })();
        """, BASE_URL, API_PATH, tests)

        market_sets = {}

        print("\n--- MARKET FILTER TEST ---")

        for result in results:
            print("\nMarket:", result["label"])
            print("HTTP status:", result.get("status"))
            print("Response bytes:", result.get("bytes"))
            print("URL:", result.get("url"))

            if result.get("status") != 200 or result.get("data") is None:
                print(
                    "Request error:",
                    result.get("error")
                    or result.get("parseError")
                    or "Unknown error",
                )
                continue

            stocks = extract_stocks(result["data"])
            market_sets[result["label"]] = stocks

            print("Unique IDs:", len(stocks))
            print(
                "First 10 symbols:",
                [x["symbol"] for x in list(stocks.values())[:10]],
            )

        print("\n--- COMPARISON ---")

        labels = list(market_sets)

        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                a = labels[i]
                b = labels[j]
                ids_a = set(market_sets[a])
                ids_b = set(market_sets[b])

                print(f"{a} vs {b}:")
                print("  Same ID set:", ids_a == ids_b)
                print("  Only in first:", len(ids_a - ids_b))
                print("  Only in second:", len(ids_b - ids_a))
                print("  Common IDs:", len(ids_a & ids_b))

        print("\nDiagnostic completed.")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
