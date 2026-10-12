import re
import html
import urllib.request
import urllib.parse
import urllib.error

PAGE_URL = "https://www.nahayatnegar.com/tv/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,*/*",
}


def fetch(url, limit=700_000, timeout=12):
    request = urllib.request.Request(
        url,
        headers=HEADERS,
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(limit)
        return response.status, response.headers, body


def normalize_url(raw_url, base_url):
    raw_url = html.unescape(raw_url.strip())
    raw_url = raw_url.replace("\\/", "/")

    # اصلاح آدرس‌های نسبی و ریشه‌ای بعد از decode کردن HTML entityها
    return urllib.parse.urljoin(base_url, raw_url)


def extract_script_urls(source):
    matches = re.findall(
        r'<script\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']',
        source,
        re.IGNORECASE,
    )

    urls = []
    for src in matches:
        url = normalize_url(src, PAGE_URL)
        if url not in urls:
            urls.append(url)

    return urls


def extract_iframes(source):
    matches = re.findall(
        r'<iframe\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']',
        source,
        re.IGNORECASE,
    )

    return [
        normalize_url(src, PAGE_URL)
        for src in matches
    ]


def inspect_js(script_url):
    print("\nSCRIPT:", script_url)

    try:
        status, headers, body = fetch(
            script_url,
            limit=700_000,
            timeout=12,
        )

        source = body.decode("utf-8", errors="replace")
        print("HTTP status:", status)
        print("Content-Type:", headers.get("Content-Type"))
        print("Bytes:", len(body))

    except Exception as exc:
        print("SCRIPT FAILED:", repr(exc))
        return set()

    keywords = re.compile(
        r"api|graphql|websocket|socket\.io|pusher|"
        r"ohlc|candlestick|historical|history|"
        r"resolution|tradingview|chart|marketdata|"
        r"subscribe|symbol|quote|price|kline",
        re.IGNORECASE,
    )

    candidates = set()

    # بررسی URLهای کامل
    full_url_pattern = (
        r"""["'`]((?:https?:)?//[^"'`\s<>]{1,300})["'`]"""
    )

    # بررسی مسیرهای نسبی یا API
    path_pattern = (
        r"""["'`]((?:/|\./)[^"'`\s<>]{1,220})["'`]"""
    )

    for pattern in (full_url_pattern, path_pattern):
        for match in re.findall(pattern, source):
            decoded = html.unescape(match).replace("\\/", "/")

            if keywords.search(decoded):
                candidates.add(
                    normalize_url(decoded, script_url)
                )

    # پیدا کردن نشانه‌های ارتباط بلادرنگ در خود کد
    for term in (
        "WebSocket",
        "socket.io",
        "pusher",
        "subscribe",
        "resolution",
        "historical",
    ):
        positions = [
            m.start()
            for m in re.finditer(
                re.escape(term),
                source,
                re.IGNORECASE,
            )
        ]

        if positions:
            print(f"Found {term}: {len(positions)} occurrence(s)")

            for pos in positions[:2]:
                excerpt = source[max(0, pos - 120):pos + 220]
                print("  CONTEXT:", excerpt[:350])

    for candidate in sorted(candidates)[:40]:
        print("CANDIDATE:", candidate)

    return candidates


def main():
    print("--- NAHAYATNEGAR CHART PAGE ---")
    print("URL:", PAGE_URL)

    try:
        status, headers, body = fetch(PAGE_URL, timeout=20)
    except Exception as exc:
        print("PAGE FAILED:", repr(exc))
        return

    page = body.decode("utf-8", errors="replace")

    title = re.search(
        r"<title[^>]*>(.*?)</title>",
        page,
        re.IGNORECASE | re.DOTALL,
    )

    print("HTTP status:", status)
    print("Content-Type:", headers.get("Content-Type"))
    print("Bytes received:", len(body))
    print(
        "Page title:",
        re.sub(r"\s+", " ", title.group(1)).strip()
        if title else "Not found",
    )

    print("\n--- IFRAMES ---")
    iframes = extract_iframes(page)

    if iframes:
        for url in iframes[:20]:
            print(url)
    else:
        print("No iframe URLs found in initial HTML.")

    print("\n--- JAVASCRIPT FILES ---")
    scripts = extract_script_urls(page)
    print("Script count:", len(scripts))

    for url in scripts:
        print(url)

    print("\n--- JAVASCRIPT INSPECTION ---")
    all_candidates = set()

    # ابتدا فایل‌های اصلی اختصاصی نمودار بررسی می‌شوند.
    priority = [
        url for url in scripts
        if any(
            term in urllib.parse.urlparse(url).path.lower()
            for term in ("base-", "feed-", "chart", "tv")
        )
    ]

    remaining = [url for url in scripts if url not in priority]
    ordered_scripts = (priority + remaining)[:10]

    for url in ordered_scripts:
        all_candidates.update(inspect_js(url))

    print("\n--- ALL CANDIDATES ---")
    if all_candidates:
        for candidate in sorted(all_candidates)[:100]:
            print(candidate)
    else:
        print("No likely chart-data routes found in inspected scripts.")

    print("\nInspection completed.")


if __name__ == "__main__":
    main()
