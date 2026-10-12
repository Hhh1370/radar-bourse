import re
import urllib.request
import urllib.parse

URL = "https://www.nahayatnegar.com/tv/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/130 Safari/537.36",
    "Accept": "text/html,application/json,*/*",
}


def fetch(url, limit=1_000_000):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as response:
        body = response.read(limit)
        return response.status, response.headers, body


def main():
    print("--- NAHAYATNEGAR CHART PAGE ---")

    try:
        status, headers, body = fetch(URL)
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
    print(
        "Page title:",
        re.sub(r"\s+", " ", title.group(1)).strip()
        if title else "Not found",
    )

    scripts = re.findall(
        r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    iframes = re.findall(
        r'<iframe\b[^>]*\bsrc=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )

    print("\n--- IFRAMES ---")
    for src in iframes[:20]:
        print(urllib.parse.urljoin(URL, src))

    print("\n--- JAVASCRIPT FILES ---")
    absolute_scripts = [
        urllib.parse.urljoin(URL, src) for src in scripts
    ]
    print("Script count:", len(absolute_scripts))

    for src in absolute_scripts[:25]:
        print(src)

    keywords = re.compile(
        r"tradingview|websocket|graphql|/api/|"
        r"chart|candlestick|ohlc|history|resolution|"
        r"getbars|subscribe|symbol|marketdata",
        re.IGNORECASE,
    )

    print("\n--- POSSIBLE DATA ROUTES ---")
    examined = 0
    found = set()

    for src in absolute_scripts:
        if examined >= 15:
            break

        parsed = urllib.parse.urlparse(src)
        if not src.lower().split("?")[0].endswith(".js"):
            continue

        try:
            js_status, js_headers, js_body = fetch(src, 700_000)
            examined += 1

            text = js_body.decode("utf-8", errors="replace")
            print(
                f"JS {examined}: HTTP {js_status}, "
                f"{len(js_body)} bytes, {parsed.netloc}"
            )

            # Find literal URLs and API-like paths in the JavaScript.
            patterns = [
                r"""["'`]((?:https?:)?//[^"'`\s]{1,220})["'`]""",
                r"""["'`]((?:/|\./)[^"'`\s]{1,180})["'`]""",
            ]

            for pattern in patterns:
                for match in re.findall(pattern, text):
                    if keywords.search(match):
                        found.add(
                            urllib.parse.urljoin(src, match)
                        )

        except Exception as exc:
            print("JS FAILED:", src, repr(exc))

    for route in sorted(found)[:80]:
        print("CANDIDATE:", route)

    print("\nInspection completed.")


if __name__ == "__main__":
    main()
