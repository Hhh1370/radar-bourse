import base64
import json
import os
import time
from datetime import datetime, timezone

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

URL = "https://rahavard365.com/marketmap"
ENDPOINT = "/api/v2/marketmap/data"


def extract_stocks(node, categories=None, results=None):
    if categories is None:
        categories = []
    if results is None:
        results = {}

    if isinstance(node, list):
        for item in node:
            extract_stocks(item, categories, results)
        return results

    if not isinstance(node, dict):
        return results

    children = node.get("children")
    node_id = node.get("id")
    name = node.get("name")
    fullname = node.get("fullname")
    has_data = "real_close_price" in node or "value" in node

    if node_id is not None and name and fullname and has_data and not children:
        raw_change = node.get("value")
        change_pct = float(raw_change) * 100 if isinstance(raw_change, (int, float)) else None
        results[str(node_id)] = {
            "id": str(node_id),
            "symbol": str(name).strip(),
            "company": str(fullname).strip(),
            "close_price": node.get("real_close_price"),
            "change_pct": change_pct,
            "area_raw": node.get("area"),
            "categories": categories[:],
        }
        return results

    next_categories = categories[:]
    if isinstance(children, list) and children and name:
        next_categories.append(str(name).strip())

    for key, value in node.items():
        if key == "children":
            extract_stocks(value, next_categories, results)
        elif isinstance(value, (dict, list)):
            extract_stocks(value, categories, results)

    return results


def get_marketmap_json(driver):
    matches = []
    for entry in driver.get_log("performance"):
        try:
            message = json.loads(entry["message"])["message"]
        except (KeyError, TypeError, ValueError):
            continue

        if message.get("method") != "Network.responseReceived":
            continue

        params = message.get("params", {})
        response = params.get("response", {})
        response_url = response.get("url", "")

        if ENDPOINT in response_url and response.get("status") == 200:
            request_id = params.get("requestId")
            if request_id:
                matches.append({"request_id": request_id, "url": response_url})

    if not matches:
        raise RuntimeError("No successful market-map API response was captured.")

    info = matches[-1]
    result = driver.execute_cdp_cmd(
        "Network.getResponseBody", {"requestId": info["request_id"]}
    )
    body = result["body"]

    if result.get("base64Encoded"):
        body = base64.b64decode(body).decode("utf-8", errors="replace")

    return info["url"], body, json.loads(body)


def main():
    chrome_path = os.environ.get("CHROME_BIN")
    driver_path = os.environ.get("CHROMEDRIVER")
    if not chrome_path or not driver_path:
        raise RuntimeError("CHROME_BIN or CHROMEDRIVER is missing.")

    options = Options()
    options.binary_location = chrome_path
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=fa-IR")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    options.add_experimental_option(
        "perfLoggingPrefs", {"enableNetwork": True, "enablePage": False}
    )

    driver = webdriver.Chrome(service=Service(driver_path), options=options)
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.set_page_load_timeout(45)
        print("Opening Rahavard market map...")
        driver.get(URL)
        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(5)

        api_url, body, source_data = get_marketmap_json(driver)
        print("API URL:", api_url)
        print("Response bytes:", len(body.encode("utf-8")))
        print("JSON root type:", type(source_data).__name__)

        stocks = sorted(
            extract_stocks(source_data).values(),
            key=lambda item: item["symbol"],
        )
        print("Extracted unique IDs:", len(stocks))
        print("Records with price:", sum(x["close_price"] is not None for x in stocks))
        print("Records with change percentage:", sum(x["change_pct"] is not None for x in stocks))

        if not stocks:
            keys = list(source_data.keys()) if isinstance(source_data, dict) else []
            print("Top-level keys:", keys)
            raise RuntimeError("API response received, but no stock records were extracted.")

        os.makedirs("data", exist_ok=True)
        payload = {
            "source": "Rahavard365 market map",
            "source_url": api_url,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "count": len(stocks),
            "items": stocks,
        }
        output_path = "data/marketmap.json"
        with open(output_path, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)

        print("Saved:", output_path)
        print("Sample records:")
        print(json.dumps(stocks[:10], ensure_ascii=False, indent=2))
        print("Market-map extraction completed successfully.")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
