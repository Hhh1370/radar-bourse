import json
import os
import time
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


URL = "https://rahavard365.com/marketmap"

KEYWORDS = (
    "api", "graphql", "marketmap", "market", "asset",
    "stock", "symbol", "ticker", "price", "quote",
    "instrument", "security", "trade", "ohlc", "rank"
)

SENSITIVE_KEYS = {
    "token", "access_token", "refresh_token",
    "authorization", "api_key", "apikey", "secret"
}


def safe_url(url):
    """Keep useful URL parameters while hiding possible secrets."""
    try:
        parts = urlparse(url)
        query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() in SENSITIVE_KEYS:
                value = "[REDACTED]"
            query.append((key, value))

        return urlunparse((
            parts.scheme,
            parts.netloc,
            parts.path,
            parts.params,
            urlencode(query),
            ""
        ))
    except Exception:
        return url[:300]


def relevant(url, resource_type):
    lower = url.lower()

    if resource_type in ("XHR", "Fetch"):
        return True

    return any(word in lower for word in KEYWORDS) and not lower.endswith(
        (".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".woff2")
    )


def main():
    options = Options()
    options.binary_location = os.environ["CHROME_BIN"]
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=fa-IR")

    # Enable Chrome's network performance log.
    options.set_capability(
        "goog:loggingPrefs",
        {"performance": "ALL"}
    )
    options.add_experimental_option(
        "perfLoggingPrefs",
        {"enableNetwork": True, "enablePage": False}
    )

    driver = webdriver.Chrome(
        service=Service(os.environ["CHROMEDRIVER"]),
        options=options,
    )

    try:
        driver.set_page_load_timeout(45)
        print("Opening Rahavard market map...")
        driver.get(URL)

        WebDriverWait(driver, 30).until(
            lambda d: len(
                d.find_elements(By.CLASS_NAME, "leaf")
            ) > 0
        )

        # Allow delayed data requests to complete.
        time.sleep(8)

        print("Page title:", driver.title)
        print("Final URL:", driver.current_url)
        print("Visible market tiles:",
              len(driver.find_elements(By.CLASS_NAME, "leaf")))

        logs = driver.get_log("performance")
        requests = {}
        responses = {}

        for entry in logs:
            try:
                message = json.loads(entry["message"])["message"]
                method = message.get("method")
                params = message.get("params", {})
                request_id = params.get("requestId")

                if method == "Network.requestWillBeSent":
                    request = params.get("request", {})
                    requests[request_id] = {
                        "url": request.get("url", ""),
                        "method": request.get("method", "GET"),
                        "type": params.get("type", ""),
                    }

                elif method == "Network.responseReceived":
                    response = params.get("response", {})
                    responses[request_id] = {
                        "status": response.get("status"),
                        "mime": response.get("mimeType", ""),
                        "url": response.get("url", ""),
                        "type": params.get("type", ""),
                    }
            except Exception:
                continue

        print("\n--- RELEVANT NETWORK REQUESTS ---")
        printed = set()
        count = 0

        for request_id, req in requests.items():
            resp = responses.get(request_id, {})
            url = req["url"] or resp.get("url", "")

            if not url or not relevant(url, req["type"] or resp.get("type", "")):
                continue

            display = (
                req["method"],
                safe_url(url),
                resp.get("status"),
                resp.get("mime", "")
            )

            if display in printed:
                continue

            printed.add(display)
            count += 1

            print(json.dumps({
                "method": display[0],
                "url": display[1],
                "status": display[2],
                "content_type": display[3],
                "resource_type": req["type"] or resp.get("type", ""),
            }, ensure_ascii=False))

            if count >= 100:
                print("Output limit reached.")
                break

        print("\nRelevant request count:", count)
        print("Total performance log entries:", len(logs))

        if count == 0:
            print(
                "No matching requests captured. "
                "The site may use another loading mechanism."
            )

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
