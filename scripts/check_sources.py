import urllib.request
import urllib.error

SOURCES = {
    "TradersArena": "https://tradersarena.ir/",
    "BourseTrader_Codal": (
        "https://bourse-trader.ir/index.php?task=codal-reports"
    ),
    "BourseTrader_Alternative": "https://fast.bourse-trader.ir/",
}

def check_source(name, url):
    print(f"\n--- {name} ---")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/json,*/*",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            body = response.read(120000)
            html = body.decode("utf-8", errors="replace")

            print("HTTP status:", response.status)
            print("Content-Type:", response.headers.get("Content-Type"))
            print("Bytes checked:", len(body))

            title = re.search(
                r"<title[^>]*>(.*?)</title>",
                html,
                re.IGNORECASE | re.DOTALL,
            )
            if title:
                print("Page title:", re.sub(r"\s+", " ", title.group(1)).strip())

            for word in ("ورود", "اشتراک", "کدال", "خطا"):
                print(f"Contains {word}:", word in html)

            print("RESULT: PAGE RECEIVED")

    except urllib.error.HTTPError as exc:
        print("HTTP ERROR:", exc.code, exc.reason)
    except Exception as exc:
        print("CONNECTION ERROR:", repr(exc))

if __name__ == "__main__":
    for name, url in SOURCES.items():
        check_source(name, url)

print("\nDiagnostic completed.")
