import re
import urllib.request
import urllib.parse
import urllib.error

URL = "https://www.nahayatnegar.com/tv/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,*/*",
}


def fetch(url, limit=700_000, timeout=8):
    request = urllib.request.Request(url, headers=HEADERS)

    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(limit)
        return (
            response.status,
            response.headers,
            body,
        )


def inspect_page():
    print("--- NAHAYATNEGAR CHART PAGE ---")
    print("URL:", URL)

    try:
        status, headers, body = fetch(URL, timeout=15)
    except Exception as exc:
        print("PAGE FAILED:", repr(exc))
        return

    html = body.decode("utf-8", errors="replace")

    print("HTTP status:", status)
    print("Content-Type:", headers.get("Content-Type"))
    print("Bytes received:", len(body))

    title = re.search(
        r"<title[^>]*>(.*?)</title>",
        html,
        re.IGNORECASE | re.DOTALL,
    )

    if title:
        page_title = re.sub(r"\s+", " ", title.group(1)).strip()
        print("Page title:", page_title)
    else:
        print("Page title: Not found")

    iframe_sources = re.findall(
        r'<iframe\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )

    script_sources = re.findall(
        r'<script\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )

    print("\n--- IFRAMES ---")
    if iframe_sources:
        for src in iframe_sources[:20]:
            print(urllib.parse.urljoin(URL, src))
    else:
        print("No iframe URLs found in initial HTML.")

    absolute_scripts = [
        urllib.parse.urljoin(URL, src)
        for src in script_sources
    ]

    print("\n--- JAVASCRIPT FILES ---")
    print("Script count:", len(absolute_scripts))

    for src in absolute_scripts[:30]:
        print(src)

    keywords = re.compile(
        r"tradingview|websocket|graphql|/api/|"
        r"chart|candlestick|ohlc|history|resolution|"
        r"getbars|subscribe|symbol|marketdata|"
        r"lightweight.?charts|kline",
        re.IGNORECASE,
    )

    patterns = [
        r"""["'`]((?:https?:)?//[^"'`\s]{1,220})["'`]""",
        r"""["'`]((?:/|\./)[^"'`\s]{1,180})["'`]""",
    ]

    print("\n--- POSSIBLE DATA ROUTES ---")

    # بررسی HTML اصلی
    candidates = set()

    for pattern in patterns:
        for match in re.findall(pattern, html):
            if keywords.search(match):
                candidates.add(urllib.parse.urljoin(URL, match))

    # بررسی حداکثر ۱۰ فایل JavaScript
    examined = 0

    for script_url in absolute_scripts:
        if examined >= 10:
            break

        clean_path = urllib.parse.urlparse(script_url).path.lower()

        if not clean_path.endswith(".js"):
            continue

        try:
            js_status, js_headers, js_body = fetch(
                script_url,
                limit=500_000,
                timeout=8,
            )

            examined += 1
            js = js_body.decode("utf-8", errors="replace")

            print(
                f"JS {examined}: HTTP {js_status}; "
                f"{len(js_body)} bytes"
            )

            for pattern in patterns:
                for match in re.findall(pattern, js):
                    if keywords.search(match):
                        candidates.add(
                            urllib.parse.urljoin(script_url, match)
                        )

        except Exception as exc:
            print(
                "JS FAILED:",
                script_url,
                repr(exc),
            )

    if candidates:
        for candidate in sorted(candidates)[:80]:
            print("CANDIDATE:", candidate)
    else:
        print("No likely chart-data URLs found in inspected files.")

    print("\nInspection completed.")


if __name__ == "__main__":
    inspect_page()
