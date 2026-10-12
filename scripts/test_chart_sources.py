
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import WebDriverException


URL = "https://www.nahayatnegar.com/tv/IRO1KSHJ0001"
WAIT_SECONDS = 25
MAX_RESPONSE_LENGTH = 3500
MAX_NETWORK_ITEMS = 150


# This script observes public page requests made by the chart.
# It does not bypass authentication, paid access, or site restrictions.
NETWORK_HOOK = r"""
(() => {
    if (window.__chartNetworkHookInstalled) return;
    window.__chartNetworkHookInstalled = true;
    window.__chartNetworkLog = [];

    const MAX_ITEMS = 150;
    const MAX_BODY = 3500;

    function addItem(item) {
        try {
            const log = window.__chartNetworkLog;
            if (log.length < MAX_ITEMS) {
                log.push({
                    time: new Date().toISOString(),
                    ...item
                });
            }
        } catch (_) {}
    }

    function shorten(value) {
        try {
            if (value === undefined || value === null) return "";
            if (typeof value !== "string") {
                value = JSON.stringify(value);
            }
            return value.slice(0, MAX_BODY);
        } catch (_) {
            return "[response could not be read]";
        }
    }

    function absoluteUrl(value) {
        try {
            return new URL(value, location.href).href;
        } catch (_) {
            return String(value || "");
        }
    }

    // Observe fetch requests and their readable responses.
    const originalFetch = window.fetch;

    if (originalFetch) {
        window.fetch = function(input, init) {
            let requestUrl = "";
            let requestMethod = "GET";

            try {
                requestUrl = typeof input === "string"
                    ? input
                    : input.url;

                requestMethod =
                    (init && init.method) ||
                    (input && input.method) ||
                    "GET";
            } catch (_) {}

            requestUrl = absoluteUrl(requestUrl);

            const started = Date.now();

            return originalFetch.apply(this, arguments).then(response => {
                try {
                    const clone = response.clone();

                    clone.text().then(body => {
                        addItem({
                            type: "fetch",
                            method: requestMethod,
                            url: requestUrl,
                            status: response.status,
                            contentType:
                                response.headers.get("content-type") || "",
                            durationMs: Date.now() - started,
                            body: shorten(body)
                        });
                    }).catch(error => {
                        addItem({
                            type: "fetch",
                            method: requestMethod,
                            url: requestUrl,
                            status: response.status,
                            error: String(error)
                        });
                    });
                } catch (error) {
                    addItem({
                        type: "fetch",
                        method: requestMethod,
                        url: requestUrl,
                        status: response.status,
                        error: String(error)
                    });
                }

                return response;
            }).catch(error => {
                addItem({
                    type: "fetch",
                    method: requestMethod,
                    url: requestUrl,
                    error: String(error)
                });
                throw error;
            });
        };
    }

    // Observe XMLHttpRequest, including JSON responses.
    const originalOpen = XMLHttpRequest.prototype.open;
    const originalSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(method, url) {
        this.__chartRequestInfo = {
            method: String(method || "GET"),
            url: absoluteUrl(url)
        };

        return originalOpen.apply(this, arguments);
    };

    XMLHttpRequest.prototype.send = function() {
        const xhr = this;
        const info = xhr.__chartRequestInfo || {
            method: "UNKNOWN",
            url: ""
        };

        const started = Date.now();

        xhr.addEventListener("loadend", function() {
            let body = "";

            try {
                if (
                    xhr.responseType === "" ||
                    xhr.responseType === "text"
                ) {
                    body = xhr.responseText;
                } else {
                    body = "[responseType=" + xhr.responseType + "]";
                }
            } catch (_) {
                body = "[response could not be read]";
            }

            let contentType = "";

            try {
                contentType = xhr.getResponseHeader("content-type") || "";
            } catch (_) {}

            addItem({
                type: "xhr",
                method: info.method,
                url: info.url,
                status: xhr.status,
                contentType: contentType,
                durationMs: Date.now() - started,
                body: shorten(body)
            });
        });

        return originalSend.apply(this, arguments);
    };
})();
"""


def make_driver():
    options = Options()

    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin and Path(chrome_bin).exists():
        options.binary_location = chrome_bin

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1440,1000")
    options.add_argument("--lang=fa-IR")
    options.add_argument("--ignore-certificate-errors")

    options.set_capability(
        "goog:loggingPrefs",
        {"performance": "ALL"}
    )

    driver_path = os.environ.get("CHROMEDRIVER")

    if driver_path and Path(driver_path).is_file():
        service = Service(executable_path=driver_path)
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(60)
    driver.set_script_timeout(30)

    return driver


def print_section(title):
    print("\n" + "=" * 20 + f" {title} " + "=" * 20)


def get_performance_requests(driver):
    """Collect request URLs, response status and WebSocket URLs."""
    requests = {}
    websocket_urls = set()

    try:
        entries = driver.get_log("performance")
    except Exception as error:
        print("Could not read Chrome performance log:", error)
        return requests, websocket_urls

    for entry in entries:
        try:
            message = json.loads(entry["message"])["message"]
            method = message.get("method", "")
            params = message.get("params", {})

            if method == "Network.requestWillBeSent":
                request = params.get("request", {})
                request_id = params.get("requestId", "")
                request_url = request.get("url", "")

                if request_url:
                    requests.setdefault(request_id, {})
                    requests[request_id].update({
                        "url": request_url,
                        "method": request.get("method", ""),
                        "resourceType": params.get("type", ""),
                    })

            elif method == "Network.responseReceived":
                response = params.get("response", {})
                request_id = params.get("requestId", "")

                requests.setdefault(request_id, {})
                requests[request_id].update({
                    "url": response.get("url", ""),
                    "status": response.get("status", ""),
                    "mimeType": response.get("mimeType", ""),
                    "resourceType": params.get("type", ""),
                })

            elif method == "Network.webSocketCreated":
                request_url = params.get("url", "")
                if request_url:
                    websocket_urls.add(request_url)

        except (KeyError, TypeError, ValueError):
            continue

    return requests, websocket_urls


def is_chart_related(url):
    parsed = urlparse(url.lower())
    text = parsed.path + "?" + parsed.query

    keywords = (
        "chart",
        "candle",
        "candles",
        "ohlc",
        "kline",
        "history",
        "historical",
        "resolution",
        "symbol",
        "quote",
        "price",
        "market",
        "feed",
        "series",
        "tradingview",
        "socket",
        "stream",
        "timescale",
        "bars",
    )

    return any(word in text for word in keywords)


def main():
    driver = None

    try:
        print("--- OPENING NAHAYATNEGAR IN CHROME ---")
        print("Target URL:", URL)

        driver = make_driver()

        # Enable browser network diagnostics.
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": NETWORK_HOOK}
        )

        try:
            driver.get(URL)
        except Exception as error:
            print("Navigation warning:", str(error)[:500])

        time.sleep(WAIT_SECONDS)

        # Scroll a little so delayed content may load.
        try:
            driver.execute_script(
                "window.scrollTo(0, document.body.scrollHeight);"
            )
            time.sleep(3)
            driver.execute_script("window.scrollTo(0, 0);")
        except Exception:
            pass

        print_section("PAGE DETAILS")

        print("Current URL:", driver.current_url)
        print("Page title:", driver.title)

        print_section("VISIBLE PAGE TEXT")

        try:
            visible_text = driver.execute_script("""
                return document.body
                    ? document.body.innerText
                    : "";
            """)
            print((visible_text or "")[:10000])
        except Exception as error:
            print("Could not read page text:", error)

        print_section("FORM CONTROLS")

        try:
            controls = driver.execute_script("""
                return Array.from(
                    document.querySelectorAll(
                        'input, select, textarea, button'
                    )
                ).slice(0, 150).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.type || "",
                    id: el.id || "",
                    name: el.name || "",
                    placeholder: el.placeholder || "",
                    text: (el.innerText || el.value || "")
                        .trim().slice(0, 150)
                }));
            """)
            print(json.dumps(
                controls,
                ensure_ascii=False,
                indent=2
            ))
        except Exception as error:
            print("Could not read form controls:", error)

        print_section("XHR / FETCH RESPONSES")

        try:
            network_log = driver.execute_script("""
                return window.__chartNetworkLog || [];
            """) or []
        except Exception as error:
            print("Could not read in-page network log:", error)
            network_log = []

        if not network_log:
            print("No XHR/fetch responses captured by the page hook.")

        for item in network_log[:MAX_NETWORK_ITEMS]:
            print(json.dumps(
                item,
                ensure_ascii=False
            ))

        # Read performance log after page load as a second data source.
        requests, websocket_urls = get_performance_requests(driver)

        print_section("WEBSOCKET CONNECTIONS")

        if websocket_urls:
            for ws_url in sorted(websocket_urls):
                print(ws_url)
        else:
            print("No WebSocket connections detected.")

        print_section("CHART-RELATED REQUEST SUMMARY")

        found = []

        # Include requests detected by Chrome, including requests that
        # the JavaScript hook may not be able to observe.
        for details in requests.values():
            request_url = details.get("url", "")
            if request_url and is_chart_related(request_url):
                found.append(details)

        # Also include intercepted fetch/XHR calls.
        for item in network_log:
            request_url = item.get("url", "")
            if request_url and is_chart_related(request_url):
                found.append({
                    "url": request_url,
                    "method": item.get("method", ""),
                    "status": item.get("status", ""),
                    "resourceType": item.get("type", ""),
                    "contentType": item.get("contentType", ""),
                })

        # Remove duplicates.
        unique = {}
        for item in found:
            key = (
                item.get("url", ""),
                str(item.get("method", "")),
                str(item.get("status", "")),
            )
            unique[key] = item

        if unique:
            for item in unique.values():
                print(json.dumps(
                    item,
                    ensure_ascii=False
                ))
        else:
            print(
                "No requests matched common chart-related keywords. "
                "Review the full XHR / FETCH RESPONSES section."
            )

        print_section("SUMMARY")

        print("XHR/fetch responses captured:", len(network_log))
        print("Chrome network requests tracked:", len(requests))
        print("WebSocket URLs detected:", len(websocket_urls))
        print("Chart-related requests:", len(unique))
        print("Inspection finished.")

    except Exception as error:
        print("TEST FAILED:", repr(error))
        raise

    finally:
        if driver is not None:
            try:
                driver.quit()
            except WebDriverException:
                pass


if __name__ == "__main__":
    main()
