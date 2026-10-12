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
        for child in node:
            extract_stocks(child, categories, results)
        return results

    if not isinstance(node, dict):
        return results

    children = node.get("children")
    name = node.get("name")
    fullname = node.get("fullname")
    node_id = node.get("id")

    # گروه‌های دارای زیرمجموعه را به‌صورت بازگشتی بررسی می‌کنیم.
    if isinstance(children, list) and children:
        next_categories = categories[:]

        if name:
            next_categories.append(str(name))

        for child in children:
            extract_stocks(child, next_categories, results)

        return results

    # فقط گره‌های انتهایی دارای شناسه و اطلاعات شرکت را ثبت می‌کنیم.
    if (
        node_id is not None
        and name
        and fullname
        and "real_close_price" in node
    ):
        symbol = str(name).strip()

        if symbol:
            results[str(node_id)] = {
                "id": str(node_id),
                "symbol": symbol,
                "company": str(fullname).strip(),
                "close_price": node.get("real_close_price"),
                "change_pct": (
                    float(node["value"]) * 100
                    if isinstance(node.get("value"), (int, float))
                    else None
                ),
                "area_raw": node.get("area"),
                "categories": categories,
            }

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
                    ENDPOINT in response_url
                    and response.get("status") == 200
                    and params.get("requestId")
                ):
                    matches.append({
                        "request_id": params["requestId"],
                        "url": response_url,
                    })

            except Exception:
                continue

        if not matches:
            raise RuntimeError(
                "No successful market-map API response was captured."
            )

        response_info = matches[-1]

        body_result = driver.execute_cdp_cmd(
            "Network.getResponseBody",
            {"requestId": response_info["request_id"]},
        )

        body = body_result["body"]

        if body_result.get("base64Encoded"):
            body = base64.b64decode(body).decode(
                "utf-8", errors="replace"
            )

        source_data = json.loads(body)

        print("API URL:", response_info["url"])
        print("Response bytes:", len(body.encode("utf-8")))

        extracted = extract_stocks(source_data)
        stocks = sorted(
            extracted.values(),
            key=lambda item: item["symbol"],
        )

        if not stocks:
            raise RuntimeError(
                "API returned data, but no stock records were identified."
            )

        payload = {
            "source": "Rahavard365 market map",
            "source_url": response_info["url"],
            "captured_at_utc": datetime.now(
                timezone.utc
            ).isoformat(),
            "count": len(stocks),
            "items": stocks,
        }

        os.makedirs("data", exist_ok=True)

        output_file = "data/marketmap.json"

        with open(output_file, "w", encoding="utf-8") as file:
            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2,
            )

        print("Extracted unique IDs:", len(stocks))
        print(
            "Records with price:",
            sum(s["close_price"] is not None for s in stocks),
        )
        print(
            "Records with change percentage:",
            sum(s["change_pct"] is not None for s in stocks),
        )
        print("Saved:", output_file)
        print("Sample records:")
        print(json.dumps(
            stocks[:10],
            ensure_ascii=False,
            indent=2,
        ))

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
