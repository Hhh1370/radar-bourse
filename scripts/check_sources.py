import base64
import json
import os
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


URL = "https://rahavard365.com/marketmap"
ENDPOINT_PART = "/api/v2/marketmap/data"


def collect_lists(obj, path="$", output=None, depth=0):
    if output is None:
        output = []

    if depth > 8:
        return output

    if isinstance(obj, dict):
        for key, value in obj.items():
            collect_lists(
                value, f"{path}.{key}", output, depth + 1
            )

    elif isinstance(obj, list):
        output.append({
            "path": path,
            "count": len(obj),
            "first": obj[0] if obj else None,
        })

        # بررسی چند مورد اول برای پیدا کردن آرایه‌های تو در تو
        for index, value in enumerate(obj[:3]):
            if isinstance(value, (dict, list)):
                collect_lists(
                    value,
                    f"{path}[{index}]",
                    output,
                    depth + 1,
                )

    return output


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

        print("Opening market map...")
        driver.get(URL)

        WebDriverWait(driver, 30).until(
            lambda d: len(
                d.find_elements(By.CLASS_NAME, "leaf")
            ) > 0
        )
        time.sleep(5)

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

                if ENDPOINT_PART in response_url:
                    matching.append({
                        "request_id": params.get("requestId"),
                        "url": response_url,
                        "status": response.get("status"),
                        "mime": response.get("mimeType"),
                    })

            except Exception:
                continue

        print("Matching API responses:", len(matching))

        successful = [
            item for item in matching
            if item["status"] == 200 and item["request_id"]
        ]

        if not successful:
            print("No successful market-data response was captured.")
            for item in matching:
                print(json.dumps(item, ensure_ascii=False))
            raise RuntimeError("Market-map API response not captured.")

        response_info = successful[-1]

        print("HTTP status:", response_info["status"])
        print("Content type:", response_info["mime"])
        print("API URL:", response_info["url"])

        body_result = driver.execute_cdp_cmd(
            "Network.getResponseBody",
            {"requestId": response_info["request_id"]},
        )

        body = body_result["body"]

        if body_result.get("base64Encoded"):
            body = base64.b64decode(body).decode(
                "utf-8", errors="replace"
            )

        data = json.loads(body)

        print("Response bytes:", len(body.encode("utf-8")))
        print("JSON root type:", type(data).__name__)

        if isinstance(data, dict):
            print("Root keys:", list(data.keys())[:50])

        elif isinstance(data, list):
            print("Root list length:", len(data))

        print("\n--- DATA ARRAYS ---")
        arrays = collect_lists(data)

        for item in sorted(
            arrays,
            key=lambda entry: entry["count"],
            reverse=True,
        )[:20]:
            print(json.dumps({
                "path": item["path"],
                "count": item["count"],
                "first_item_type": (
                    type(item["first"]).__name__
                    if item["first"] is not None
                    else None
                ),
            }, ensure_ascii=False))

        record_arrays = [
            item for item in arrays
            if isinstance(item["first"], dict)
        ]

        if record_arrays:
            largest = max(
                record_arrays,
                key=lambda item: item["count"],
            )

            print("\n--- LARGEST RECORD ARRAY ---")
            print("Path:", largest["path"])
            print("Count:", largest["count"])
            print("Sample records:")

            print(json.dumps(
                # فقط سه رکورد برای جلوگیری از خروجی بسیار طولانی
                largest["first"],
                ensure_ascii=False,
                indent=2,
            )[:6000])

        else:
            print("\nNo list of record objects identified.")

        print("\nInspection completed.")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
