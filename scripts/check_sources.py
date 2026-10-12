import json
import os
import time
from datetime import datetime, timezone

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

BASE_URL = "https://rahavard365.com"
PAGE_URL = f"{BASE_URL}/marketmap"
API_URL = (
    f"{BASE_URL}/api/v2/marketmap/data"
    "?asset_id=1&coloring_type_id=1"
    "&size_type_id=1&category_id=1"
)


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
        "real_close_price" in node
        or "value" in node
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

    for key, value in node.items():
        if key != "children":
            extract_stocks(value, categories, results)

    return results


def main():
    chrome_path = os.environ.get("CHROME_BIN")
    driver_path = os.environ.get("CHROMEDRIVER")

    if not chrome_path or not driver_path:
        raise RuntimeError("Chrome یا ChromeDriver تنظیم نشده است.")

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

        print("Opening public Rahavard market map...")
        driver.get(PAGE_URL)

        WebDriverWait(driver, 30).until(
            lambda browser: browser.execute_script(
                "return document.readyState"
            ) == "complete"
        )

        time.sleep(3)

        result = driver.execute_async_script("""
            const url = arguments[0];
            const done = arguments[arguments.length - 1];

            fetch(url, {
                method: "GET",
                credentials: "include",
                cache: "no-store",
                headers: {"Accept": "application/json"}
            })
            .then(async response => ({
                status: response.status,
                body: await response.text()
            }))
            .then(done)
            .catch(error => done({error: String(error)}));
        """, API_URL)

        if result.get("error"):
            raise RuntimeError(
                "دریافت داده ناموفق بود: " + result["error"]
            )

        print("HTTP status:", result["status"])

        if result["status"] != 200:
            raise RuntimeError(
                f"API پاسخ HTTP {result['status']} داد؛ "
                "فایل قبلی حفظ می‌شود."
            )

        try:
            source_data = json.loads(result["body"])
        except json.JSONDecodeError as exc:
            raise RuntimeError("پاسخ API، JSON معتبر نیست.") from exc

        stocks = sorted(
            extract_stocks(source_data).values(),
            key=lambda item: item["symbol"],
        )

        if not stocks:
            raise RuntimeError(
                "هیچ نمادی استخراج نشد؛ فایل قبلی حفظ می‌شود."
            )

        os.makedirs("data", exist_ok=True)

        payload = {
            "source": "Rahavard365 public market-map API",
            "scope": (
                "خروجی عمومی نقشه بازار؛ "
                "تفکیک بورس و فرابورس مستقلاً تأیید نشده است"
            ),
            "completeness_verified": False,
            "captured_at_utc": datetime.now(
                timezone.utc
            ).isoformat(),
            "count": len(stocks),
            "records_with_price": sum(
                item["close_price"] is not None for item in stocks
            ),
            "records_with_change_pct": sum(
                item["change_pct"] is not None for item in stocks
            ),
            "items": stocks,
        }

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

        print("Extracted unique IDs:", len(stocks))
        print("Records with price:", payload["records_with_price"])
        print(
            "Records with change percentage:",
            payload["records_with_change_pct"],
        )
        print("Saved: data/marketmap.json")

        print("Sample:")
        print(json.dumps(
            stocks[:5],
            ensure_ascii=False,
            indent=2,
        ))

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
