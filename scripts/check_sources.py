import base64
import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

BASE_URL = "https://rahavard365.com/marketmap"
ENDPOINT = "/api/v2/marketmap/data"

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

    if (
        node_id is not None
        and name
        and fullname
        and (
            "real_close_price" in node
            or "value" in node
        )
    ):
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


def fetch_exchange(driver, exchange_id, exchange_name):
    url = (
        f"{BASE_URL}?asset_id={exchange_id}"
        "&coloring_type_id=1&size_type_id=1&category_id=1"
    )

    # پاک کردن گزارش درخواست‌های اجرای قبلی
    driver.get_log("performance")

    print(f"\n--- دریافت {exchange_name} ---")
    driver.get(url)

    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script(
            "return document.readyState"
        ) == "complete"
    )

    time.sleep(5)

    logs = driver.get_log("performance")
    matches = []

    for entry in logs:
        try:
            message = json.loads(entry["message"])["message"]

            if message.get("method") != "Network.responseReceived":
                continue

            params = message.get("params", {})
            response = params.get("response", {})
            response_url = response.get("url", "")

            if (
                ENDPOINT not in response_url
                or response.get("status") != 200
                or not params.get("requestId")
            ):
                continue

            query = parse_qs(urlparse(response_url).query)
            actual_exchange = query.get("asset_id", [""])[0]

            if actual_exchange == exchange_id:
                matches.append({
                    "request_id": params["requestId"],
                    "url": response_url,
                })

        except (KeyError, TypeError, ValueError):
            continue

    if not matches:
        raise RuntimeError(
            f"پاسخ معتبر API برای {exchange_name} پیدا نشد."
        )

    response_info = matches[-1]

    result = driver.execute_cdp_cmd(
        "Network.getResponseBody",
        {"requestId": response_info["request_id"]},
    )

    body = result["body"]

    if result.get("base64Encoded"):
        body = base64.b64decode(body).decode(
            "utf-8",
            errors="replace",
        )

    data = json.loads(body)
    stocks = extract_stocks(data)

    if not stocks:
        raise RuntimeError(
            f"از پاسخ {exchange_name} هیچ نمادی استخراج نشد."
        )

    records = list(stocks.values())

    for record in records:
        record["exchange_id"] = exchange_id
        record["exchange"] = exchange_name

    print("API URL:", response_info["url"])
    print("تعداد شناسه‌های یکتا:", len(records))
    print(
        "دارای قیمت:",
        sum(x["close_price"] is not None for x in records),
    )
    print(
        "دارای درصد تغییر:",
        sum(x["change_pct"] is not None for x in records),
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

    options.set_capability(
        "goog:loggingPrefs",
        {"performance": "ALL"},
    )
    options.add_experimental_option(
        "perfLoggingPrefs",
        {"enableNetwork": True, "enablePage": False},
    )

    driver = webdriver.Chrome(
        service=Service(driver_path),
        options=options,
    )

    try:
        driver.execute_cdp_cmd("Network.enable", {})

        all_records = {}

        for exchange_id, exchange_name in EXCHANGES.items():
            records = fetch_exchange(
                driver,
                exchange_id,
                exchange_name,
            )

            for record in records:
                # اگر شناسه تکراری باشد، فقط یک رکورد نگه می‌داریم.
                all_records[record["id"]] = record

        stocks = sorted(
            all_records.values(),
            key=lambda x: x["symbol"],
        )

        payload = {
            "source": "Rahavard365 market map",
            "captured_at_utc": datetime.now(
                timezone.utc
            ).isoformat(),
            "exchange_counts": {
                "بورس": sum(
                    x["exchange_id"] == "1" for x in stocks
                ),
                "فرابورس": sum(
                    x["exchange_id"] == "2" for x in stocks
                ),
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

        print("\n--- نتیجهٔ نهایی ---")
        print("تعداد بورس:", payload["exchange_counts"]["بورس"])
        print("تعداد فرابورس:", payload["exchange_counts"]["فرابورس"])
        print("تعداد کل پس از حذف تکراری‌ها:", len(stocks))
        print("ذخیره شد: data/marketmap.json")

        print("\nنمونه:")
        print(json.dumps(
            stocks[:5],
            ensure_ascii=False,
            indent=2,
        ))

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
