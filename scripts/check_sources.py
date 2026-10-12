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
    """Extract stock records recursively from Rahavard's market-map JSON."""

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

    # Traverse industry/group nodes and their children.
    if isinstance(children, list) and children:
        next_categories = list(categories)

        if name:
            next_categories.append(str(name).strip())

        for child in children:
            extract_stocks(child, next_categories, results)

        return results

    # Identify a leaf record that looks like a stock.
    has_market_data = (
        "real_close_price" in node
        or "value" in node
    )

    if (
        node_id is not None
        and name
        and fullname
        and has_market_data
    ):
        symbol = str(name).strip()

        if symbol:
            raw_change = node.get("value")
            change_pct = None

            if isinstance(raw_change, (int, float)):
                # API value is a decimal, e.g. 0.0299 means 2.99%.
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

    # Important: the API response has wrapper levels such as
    # data -> root -> children. Traverse those wrapper objects too.
    for key, value in node.items():
        if key != "children":
            extract_stocks(value, categories, results)

    return results


def capture_market_map(driver):
    """Capture the market-map JSON response from Chrome's network log."""

    logs = driver.get_log("performance")
    matching = []

    for entry in logs:
        try:
            message = json.loads(entry["message"])["message"]

            if message.get("method") != "Network.responseReceived":
                continue

            params = message.get("params", {})
            response = params.get("response", {})
            response_url = response.get("url", "")

            if (
                ENDPOINT in response_url
                and response.get("status") == 200
                and params.get("requestId")
            ):
                matching.append({
                    "request_id": params["requestId"],
                    "url": response_url,
                    "status": response.get("status"),
                    "mime": response.get("mimeType", ""),
                })

        except (KeyError, TypeError, ValueError):
            continue

    if not matching:
        raise RuntimeError(
            "No successful market-map API response was captured."
        )

    # Use the most recent matching successful response.
    response_info = matching[-1]

    body_result = driver.execute_cdp_cmd(
        "Network.getResponseBody",
        {"requestId": response_info["request_id"]},
    )

    body = body_result["body"]

    if body_result.get("base64Encoded"):
        body = base64.b64decode(body).decode(
            "utf-8",
            errors="replace",
        )

    try:
        source_data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "The market-map response was not valid JSON."
        ) from exc

    return response_info, body, source_data


def main():
    chrome_path = os.environ.get("CHROME_BIN")
    driver_path = os.environ.get("CHROMEDRIVER")

    if not chrome_path or not driver_path:
        raise RuntimeError(
            "CHROME_BIN or CHROMEDRIVER environment variable is missing."
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
        driver.set_page_load_timeout(45)

        print("Opening Rahavard market map...")
        driver.get(URL)

        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script(
                "return document.readyState"
            ) == "complete"
        )

        time.sleep(5)

        print("Page title:", driver.title)

        response_info, body, source_data = capture_market_map(driver)

        print("API URL:", response_info["url"])
        print("HTTP status:", response_info["status"])
        print("Content type:", response_info["mime"])
        print("Response bytes:", len(body.encode("utf-8")))

        if isinstance(source_data, dict):
            print("JSON root keys:", list(source_data.keys()))
        else:
            print("JSON root type:", type(source_data).__name__)

        extracted = extract_stocks(source_data)

        stocks = sorted(
            extracted.values(),
            key=lambda item: item["symbol"],
        )

        with_price = sum(
            stock["close_price"] is not None
            for stock in stocks
        )

        with_change = sum(
            stock["change_pct"] is not None
            for stock in stocks
        )

        print("Extracted unique IDs:", len(stocks))
        print("Records with price:", with_price)
        print("Records with change percentage:", with_change)

        if not stocks:
            raise RuntimeError(
