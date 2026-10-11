import json
import urllib.request
import urllib.error

SOURCES = {
    "TSETMC": (
        "https://cdn.tsetmc.com/api/ClosingPrice/"
        "GetMarketWatch?market=0&withBestLimits=false&hEven=0&RefID=0"
    ),
    "CODAL": (
        "https://search.codal.ir/api/search/v2/q?"
        "Symbol=%D9%81%D9%85%D9%84%DB%8C&PageNumber=1"
    ),
}

def check_source(name, url):
    print(f"\n--- {name} ---")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            print("HTTP status:", response.status)
            print("Response bytes:", len(body))

            data = json.loads(body.decode("utf-8-sig"))
            print("Valid JSON: YES")

            if isinstance(data, dict):
                print("Top-level keys:", list(data.keys())[:15])
            elif isinstance(data, list):
                print("Items returned:", len(data))
            else:
                print("Response type:", type(data).__name__)

    except Exception as exc:
        print("CHECK FAILED:", repr(exc))

if __name__ == "__main__":
    for name, url in SOURCES.items():
        check_source(name, url)
