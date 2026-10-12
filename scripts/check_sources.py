import json
import os
import time

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
            "close_price": node.get("real_close_price"),
            "value": node.get("value"),
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
        driver.set_script_timeout(40)
        driver.get(PAGE_URL)

        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script(
                "return document.readyState"
            ) == "complete"
        )
        time.sleep(3)

        results = driver.execute_async_script("""
            const base = arguments[0];
            const apiPath = arguments[1];
            const done = arguments[arguments.length - 1];

            (async () => {
                const output = [];

                for (const marketId of ["1", "2", "ALL"]) {
                    const params = new URLSearchParams({
                        asset_id: "1",
                        coloring_type_id: "1",
                        size_type_id: "1",
                        category_id: "1"
                    });

                    if (marketId !== "ALL") {
                        params.set("market_id", marketId);
                    }

                    const url = base + apiPath + "?" + params.toString();

                    try {
                        const response = await fetch(url, {
                            credentials: "include",
                            cache: "no-store",
                            headers: {"Accept": "application/json"}
                        });

                        const text = await response.text();

                        let data;
                        try {
                            data = JSON.parse(text);
                        } catch (e) {
                            output.push({
                                market: marketId,
                                status: response.status,
                                error: "Invalid JSON"
                            });
                            continue;
                        }

                        output.push({
                            market: marketId,
                            status: response.status,
                            bytes: new TextEncoder().encode(text).length,
                            data: data
                        });
                    } catch (e) {
                        output.push({
                            market: marketId,
                            error: String(e)
                        });
                    }
                }

                done(output);
            })();
        """, BASE_URL, API_PATH)

        market_sets = {}

        print("--- CORRECT MARKET FILTER TEST ---")

        for result in results:
            market = result["market"]
            print("\nMarket:", market)
            print("HTTP status:", result.get("status"))
            print("Response bytes:", result.get("bytes"))

            if result.get("status") != 200 or "data" not in result:
                print("ERROR:", result.get("error", "Request failed"))
                continue

            stocks = extract_stocks(result["data"])
            market_sets[market] = stocks

            print("Unique IDs:", len(stocks))
            print(
                "First 10 symbols:",
                [x["symbol"] for x in list(stocks.values())[:10]]
            )

        print("\n--- COMPARISON ---")

        if "1" in market_sets and "2" in market_sets:
            a = set(market_sets["1"])
            b = set(market_sets["2"])

            print("Only in بورس:", len(a - b))
            print("Only in فرابورس:", len(b - a))
            print("Common IDs:", len(a & b))
            print("Same complete ID set:", a == b)

        if "ALL" in market_sets:
            print("All-market IDs:", len(market_sets["ALL"]))

        print("\nDiagnostic completed.")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
