import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait


BASE_URL = "https://rahavard365.com"
PAGE_URL = f"{BASE_URL}/marketmap"
API_PATH = "/api/v2/marketmap/data"

EXCHANGES = {
    "1": "بورس",
    "2": "فرابورس",
}


def extract_stocks(node, categories=None, results=None):
    if categories is None:
        categories = []

    if results is None:
        results = {}

    if isinstance(node, list):
        for child in node:
            extract_stocks(child, categories, results)
        return results

    if not isinstance(node, dict):
        return results

    children = node.get("children")
    name = node.get("name")
    fullname = node.get("fullname")
    node_id = node.get("id")

    if isinstance(children, list) and children:
        next_categories = list(categories)

        if name:
            next_categories.append(str(name).strip())

        for child in children:
            extract_stocks(child, next_categories, results)

        return results

    has_market_data = (
        "real_close_price" in node or "value" in node
    )

    if node_id is not None and name and fullname and has_market_data:
        symbol = str(name).strip()

        if symbol:
            raw_change = node.get("value")
            change_pct = None

            if isinstance(raw_change, (int, float)):
                change_pct = float(raw_change) * 100

            results[str(node_id)] = {
                "id": str(node_id),
                "symbol": symbol,
                "company": str(fullname).strip(),
                "close_price": node.get("real_close_price"),
                "change_pct": change_pct,
                "area_raw": node.get("area"),
                "categories": categories,
            }

        return results

    # عبور از لایه‌های بیرونی مانند data و root
    for key, value in node.items():
        if key != "children":
            extract_stocks(value, categories, results)

    return results


def fetch_exchange(driver, exchange_id, exchange_name):
    params = {
        "asset_id": exchange_id,
        "coloring_type_id": "1",
        "size_type_id": "1",
        "category_id": "1",
    }

    api_url = f"{BASE_URL}{API_PATH}?{urlencode(params)}"

    # درخواست مستقیم در مرورگر بازشدهٔ ره‌آورد
    result = driver.execute_async_script("""
        const url = arguments[0];
        const done = arguments[arguments.length - 1];

        fetch(url, {
            method: "GET",
            credentials: "include",
            headers: {"Accept": "application/json"}
        })
        .then(async response => {
            const body = await response.text();
            done({
                status: response.status,
                contentType: response.headers.get("content-type"),
                body: body
            });
        })
        .catch(error => {
            done({error: String(error)});
        });
    """, api_url)

    if result.get("error"):
        raise RuntimeError(
            f"خطای درخواست {exchange_name}: {result['error']}"
        )

    status = result.get("status")

    print(f"\n--- {exchange_name} ---")
    print("Requested API:", api_url)
    print("HTTP status:", status)

    if status != 200:
        raise RuntimeError(
            f"API برای {exchange_name} پاسخ HTTP {status} داد."
        )

    try:
        data = json.loads(result["body"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"پاسخ {exchange_name} JSON معتبر نیست."
        ) from exc

    stocks = extract_stocks(data)

    if not stocks:
        print("Top-level keys:", list(data.keys()) if isinstance(data, dict) else type(data).__name__)
        raise RuntimeError(
            f"هیچ نمادی از پاسخ {exchange_name} استخراج نشد."
        )

    records = list(stocks.values())

    for record in records:
        record["exchange_id"] = exchange_id
        record["exchange"] = exchange_name

    print("Extracted unique IDs:", len(records))
    print(
        "Records with price:",
        sum(item["close_price"] is not None for item in records),
    )
    print(
        "Records with change percentage:",
        sum(item["change_pct"] is not None for item in records),
    )

    return records


def main():
    chrome_path = os.environ.get("CHROME_BIN")
    driver_path = os.environ.get("CHROMEDRIVER")

    if not chrome_path or not driver_path:
        raise RuntimeError(
            "مسیر Chrome یا ChromeDriver تنظیم نشده است."
        )

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
        driver.set_script_timeout(35)

        print("Opening Rahavard market map...")
        driver.get(PAGE_URL)

        WebDriverWait(driver, 30).until(
            lambda browser: browser.execute_script(
                "return document.readyState"
            ) == "complete"
        )

        time.sleep(3)
        print("Page title:", driver.title)

        # هر دو درخواست باید موفق شوند؛ در غیر این صورت فایل
        # خروجی جدید ساخته نمی‌شود تا اطلاعات ناقص جایگزین نشود.
        all_records = {}

        for exchange_id, exchange_name in EXCHANGES.items():
            records = fetch_exchange(
                driver,
                exchange_id,
                exchange_name,
            )

            for record in records:
                stock_id = record["id"]

                if stock_id in all_records:
                    all_records[stock_id]["exchanges_seen"].append(
                        exchange_name
                    )
                else:
                    record["exchanges_seen"] = [exchange_name]
                    all_records[stock_id] = record

        stocks = sorted(
            all_records.values(),
            key=lambda item: item["symbol"],
        )

        payload = {
            "source": "Rahavard365 market-map API",
            "captured_at_utc": datetime.now(
                timezone.utc
            ).isoformat(),
            "exchange_counts": {
                name: sum(
                    name in item["exchanges_seen"]
                    for item in stocks
                )
                for name in EXCHANGES.values()
            },
            "count": len(stocks),
            "items": stocks,
        }

        os.makedirs("data", exist_ok=True)

        with open(
            "data/marketmap.json",
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2,
            )

        print("\n--- FINAL RESULT ---")
        print("بورس:", payload["exchange_counts"]["بورس"])
        print("فرابورس:", payload["exchange_counts"]["فرابورس"])
        print("مجموع شناسه‌های یکتا:", len(stocks))
        print("Saved: data/marketmap.json")

        print("\nSample records:")
        print(json.dumps(
            stocks[:5],
            ensure_ascii=False,
            indent=2,
        ))

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
