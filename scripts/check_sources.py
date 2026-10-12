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


def collect_stock_nodes(node, results=None):
    if results is None:
        results = {}

    if isinstance(node, list):
        for item in node:
            collect_stock_nodes(item, results)
        return results

    if not isinstance(node, dict):
        return results

    children = node.get("children")

    if isinstance(children, list):
        for child in children:
            collect_stock_nodes(child, results)
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

    # پشتیبانی از لایه‌های بیرونی مثل data و root
    for key, value in node.items():
        if key != "children":
            collect_stock_nodes(value, results)

    return results


def main():
    chrome_path = os.environ["CHROME_BIN"]
    driver_path = os.environ["CHROMEDRIVER"]

    options = Options()
    options.binary_location = chrome_path
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=fa-IR")

    driver = webdriver.Chrome(
        service=Service(driver_path),
        options=options,
    )

    try:
        driver.set_page_load_timeout(45)
        driver.set_script_timeout(40)

        print("Opening Rahavard...")
        driver.get(PAGE_URL)

        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script(
                "return document.readyState"
            ) == "complete"
        )
        time.sleep(3)

        variants = [
            (
                "asset_id=1",
                {
                    "asset_id": "1",
                    "coloring_type_id": "1",
                    "size_type_id": "1",
                    "category_id": "1",
                },
            ),
            (
                "asset_id=2",
                {
                    "asset_id": "2",
                    "coloring_type_id": "1",
                    "size_type_id": "1",
                    "category_id": "1",
                },
            ),
            (
                "asset_id omitted",
                {
                    "coloring_type_id": "1",
                    "size_type_id": "1",
                    "category_id": "1",
                },
            ),
            (
                "exchange_id=1",
                {
                    "asset_id": "1",
                    "exchange_id": "1",
                    "coloring_type_id": "1",
                    "size_type_id": "1",
                    "category_id": "1",
                },
            ),
            (
                "exchange_id=2",
                {
                    "asset_id": "1",
                    "exchange_id": "2",
                    "coloring_type_id": "1",
                    "size_type_id": "1",
                    "category_id": "1",
                },
            ),
        ]

        results = driver.execute_async_script("""
            const variants = arguments[0];
            const base = arguments[1];
            const done = arguments[arguments.length - 1];

            (async () => {
                const output = [];

                for (const variant of variants) {
                    const params = new URLSearchParams(variant.params);
                    const url = base + "?" + params.toString();

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
                            label: variant.label,
                            url: url,
                            status: response.status,
                            bytes: new TextEncoder().encode(body).length,
                            data: data,
                            parseError: parseError
                        });
                    } catch (error) {
                        output.push({
                            label: variant.label,
                            url: url,
                            error: String(error)
                        });
                    }
                }

                done(output);
            })();
        """, [
            {"label": label, "params": params}
            for label, params in variants
        ], API_PATH)

        print("\n--- API COMPARISON ---")

        signatures = {}

        for result in results:
            print("\nRequest:", result["label"])
            print("HTTP status:", result.get("status"))
            print("Response bytes:", result.get("bytes"))

            data = result.get("data")
            if not isinstance(data, (dict, list)):
                print("Response error:", result.get("error") or result.get("parseError"))
                continue

            stocks = collect_stock_nodes(data)
            stock_list = sorted(
                stocks.values(),
                key=lambda item: item["symbol"],
            )

            signature = tuple(
                (item["id"], item["symbol"])
                for item in stock_list
            )
            signatures[result["label"]] = signature

            print("Unique stock IDs:", len(stock_list))
            print("First 10 symbols:", [
                item["symbol"] for item in stock_list[:10]
            ])
            print("Last 5 symbols:", [
                item["symbol"] for item in stock_list[-5:]
            ])

        print("\n--- COMPARISON SUMMARY ---")
        labels = list(signatures)

        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                a = labels[i]
                b = labels[j]
                print(
                    f"{a} == {b}:",
                    signatures[a] == signatures[b],
                )

        print("\nDiagnostic completed.")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
