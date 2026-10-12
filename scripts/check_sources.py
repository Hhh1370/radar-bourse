import re
import urllib.request
import urllib.parse

SOURCES = {
    "TradersArena": "https://tradersarena.ir/",
    "BourseTrader_Codal": (
        "https://bourse-trader.ir/index.php?task=codal-reports"
        "Rahavard_Home": "https://rahavard365.com/",
        "Rahavard_MarketMap": "https://rahavard365.com/marketmap",
    ),
}

LIBRARIES = (
    "jquery", "bootstrap", "apexcharts", "chart",
    "sortable", "draggable", "tweenmax", "popper",
    "sweetalert", "lightweight-charts", "farsiinput",
)

def inspect(name, page_url):
    print(f"\n--- {name} ---")

    request = urllib.request.Request(
        page_url,
        headers={"User-Agent": "Mozilla/5.0"},
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            html = response.read(500000).decode(
                "utf-8", errors="replace"
            )

        scripts = re.findall(
            r'<script\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )

        print("Scripts found:", len(scripts))

        for i, src in enumerate(scripts, 1):
            url = urllib.parse.urljoin(page_url, src)
            path = urllib.parse.urlparse(url).path.lower()

            if any(word in path for word in LIBRARIES):
                kind = "LIBRARY"
            else:
                kind = "CHECK THIS"

            print(f"{i}. [{kind}] {url}")

        print("Page received: YES")

    except Exception as exc:
        print("FAILED:", repr(exc))

if __name__ == "__main__":
    for name, url in SOURCES.items():
        inspect(name, url)

    print("\nInspection completed.")
