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

    # پیمایش فهرست‌ها
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

    # گروه صنعت یا گروهی که نمادهای زیرمجموعه دارد
    if isinstance(children, list) and children:
        next_categories = list(categories)

        if name:
            next_categories.append(str(name).strip())

        for child in children:
            extract_stocks(child, next_categories, results)

        return results

    # شناسایی رکورد انتهایی نماد
    is_stock = (
        node_id is not None
        and name
        and fullname
        and (
            "real_close_price" in node
            or "value" in node
        )
    )

    if is_stock:
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

    # نکتهٔ اصلاح‌شده:
    # از لایه‌های بیرونی مثل data و root نیز عبور می‌کنیم.
    for key, value in node.items():
        if key != "children":
            extract_stocks(value, categories, results)

    return results


def main():
    options = Options()
    options.binary_location = os.environ["CHROME_BIN"]
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
        service=Service(os.environ["CHROMEDRIVER"]),
        options=options,
    )

    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.set_page_load_timeout(45)

        print("Opening Rahavard market map...")
        driver.get(URL)

        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script(
                "return document.readyState"
            ) == "complete"
        )

        WebDriverWait(driver, 30).until(
            lambda d: len(
                d.find_elements(
                    "class name", "leaf"
                )
            ) > 0
        )

        time.sleep(5)

        logs = driver.get_log("performance")
        matching = []

        for entry in logs:
            try:
                message = json.loads(
                    entry["message"]
                )["message"]

                if message.get("method") != "Network.responseReceived":
                    continue

                params = message.get("params", {})
                response = params.get("response", {})

                if (
                    ENDPOINT in response.get("url", "")
                    and response.get("status") == 200
                    and params.get("requestId")
