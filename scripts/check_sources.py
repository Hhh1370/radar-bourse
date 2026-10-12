import re
import urllib.request

TARGETS = {
    "Rahavard_MarketMap": (
        "https://rahavard365.com/_next/static/chunks/"
        "pages/marketmap-69b3a67ddee0569b.js"
    ),
    "Rahavard_Shared": (
        "https://rahavard365.com/_next/static/chunks/"
        "7079-45905514788180e4.js"
    ),
    "Rahavard_Home": (
        "https://rahavard365.com/_next/static/chunks/"
        "pages/index-7523a1e566132b2c.js"
    ),
    "TradersArena_Product": (
        "https://tradersarena.ir/js/product/index.5.0.7.min.js"
    ),
    "TradersArena_Search": (
        "https://tradersarena.ir/js/product/search.1.0.3.min.js"
    ),
    "TradersArena_Watch": (
        "https://tradersarena.ir/js/watch-export.js"
    ),
    "BourseTrader": (
        "https://bourse-trader.ir/js/script.js?ver=11"
    ),
}

KEYWORDS = re.compile(
    r"api|graphql|ajax|marketmap|market|stock|symbol|"
    r"ticker|price|trade|codal|instrument|quote|"
    r"watchlist|history|ohlc|security|financial",
    re.IGNORECASE,
)

STRING_URL = re.compile(
    r"""["'`]((?:https?://|//|/)[^"'`\s]{1,220})["'`]"""
)

def inspect(name, url):
    print(f"\n--- {name} ---")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            source = response.read(800000).decode(
                "utf-8", errors="replace"
            )

        source = source.replace("\\/", "/")
        print("HTTP status: 200")
        print("Bytes read:", len(source))

        matches = {
            item for item in STRING_URL.findall(source)
            if KEYWORDS.search(item)
        }

        if matches:
            for item in sorted(matches)[:30]:
                print("CANDIDATE:", item)
        else:
            print("No matching endpoint strings found.")

    except Exception as exc:
        print("FETCH FAILED:", repr(exc))

if __name__ == "__main__":
    for name, url in TARGETS.items():
        inspect(name, url)

    print("\nInspection completed.")
