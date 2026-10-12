import os
import json
import time
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait


PAGE_URL = "https://www.nahayatnegar.com/tv/"

SENSITIVE_KEYS = {
    "token", "access_token", "refresh_token",
    "authorization", "api_key", "apikey", "secret",
    "password", "session",
}

INTERESTING_WORDS = (
    "chart", "history", "historical", "ohlc", "candle",
    "symbol", "market", "quote", "price", "resolution",
    "feed", "bar", "trade", "stock", "websocket",
    "socket.io", "graphql", "/api/",
)


def safe_url(url):
    """Hide likely credentials in query parameters."""
    try:
        parts = urlparse(url)
        safe_query = []

        for key, value in parse_qsl(
            parts.query, keep_blank_values=True
        ):
            if key.lower() in SENSITIVE_KEYS:
                value = "[REDACTED]"
            safe_query.append((key, value))

        return urlunparse((
            parts.scheme,
            parts.netloc,
            parts.path,
            parts.params,
            urlencode(safe_query),
            "",
        ))
    except Exception:
        return url[:300]


def main():
    chrome_path = os.environ.get("CHROME_BIN")
    driver_path = os.environ.get("CHROMEDRIVER")

    if not chrome_path or not driver_path:
        raise RuntimeError(
            "CHROME_BIN or CHROMEDRIVER is not configured."
        )

    options = Options()
    options.binary_location = chrome_path
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1365,900")
    options.add_argument("--lang=fa-IR")

    options.set_capability(
        "goog:loggingPrefs",
        {"performance": "ALL"},
    )

    driver = webdriver.Chrome(
        service=Service(driver_path),
        options=options,
    )

    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.set_page_load_timeout(45)

        print("--- OPENING NAHAYATNEGAR IN CHROME ---")
        driver.get(PAGE_URL)

        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script(
                "return document.readyState"
            ) == "complete"
        )

        # Give the page's own JavaScript time to start requests.
        time.sleep(12)

        print("Page title:", driver.title)
        print("Final URL:", driver.current_url)

        print("\n--- VISIBLE PAGE TEXT ---")
        try:
            print(
                driver.find_element("tag name", "body")
                .text[:2500]
            )
        except Exception as exc:
            print("Could not read page text:", repr(exc))

        print("\n--- FORM CONTROLS ---")

        for element in driver.find_elements(
            "css selector", "input, select, button"
        )[:60]:
            info = {
                "tag": element.tag_name,
                "type": element.get_attribute("type"),
                "id": element.get_attribute("id"),
                "name": element.get_attribute("name"),
                "placeholder": element.get_attribute("placeholder"),
                "text": element.text.strip()[:100],
            }

            if element.tag_name == "select":
                info["options"] = [
                    option.text.strip()
                    for option in element.find_elements(
                        "tag name", "option"
                    )[:20]
                ]

            print(json.dumps(info, ensure_ascii=False))

        logs = driver.get_log("performance")
        requests = {}
        responses = {}
        websockets = set()

        for entry in logs:
            try:
                message = json.loads(
                    entry["message"]
                )["message"]

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
                        "url": response.get("url", ""),
                        "status": response.get("status"),
                        "mime": response.get("mimeType", ""),
                        "type": params.get("type", ""),
                    }

                elif method == "Network.webSocketCreated":
                    url = params.get("url", "")
                    if url:
                        websockets.add(url)

            except Exception:
                continue

        print("\n--- XHR / FETCH RESPONSES ---")

        xhr_count = 0

        for request_id, request in requests.items():
            response = responses.get(request_id, {})
            resource_type = (
                request.get("type")
                or response.get("type")
                or ""
            )

            if resource_type not in ("XHR", "Fetch"):
                continue

            url = request.get("url") or response.get("url", "")
            if not url:
                continue

            print(json.dumps({
                "method": request.get("method"),
                "url": safe_url(url),
                "status": response.get("status"),
                "content_type": response.get("mime"),
            }, ensure_ascii=False))

            xhr_count += 1

            if xhr_count >= 100:
                print("XHR/FETCH output limit reached.")
                break

        print("\nXHR/FETCH response count:", xhr_count)

        print("\n--- WEBSOCKET CONNECTIONS ---")

        if websockets:
            for url in sorted(websockets):
                print(safe_url(url))
        else:
            print("No WebSocket creation event captured.")

        print("\n--- CHART-RELATED REQUEST SUMMARY ---")

        count = 0

        for request_id, request in requests.items():
            response = responses.get(request_id, {})
            url = request.get("url") or response.get("url", "")

            if not url:
                continue

            if any(
                word in url.lower()
                for word in INTERESTING_WORDS
            ):
                print(json.dumps({
                    "method": request.get("method"),
                    "url": safe_url(url),
                    "status": response.get("status"),
                    "content_type": response.get("mime"),
                    "resource_type": (
                        request.get("type")
                        or response.get("type")
                    ),
                }, ensure_ascii=False))

                count += 1

                if count >= 100:
                    print("Chart-related output limit reached.")
                    break

        print("\nChart-related request count:", count)
        print("Network log entries:", len(logs))
        print("\nInspection completed.")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
