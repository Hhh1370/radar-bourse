import json
import urllib.request
import urllib.parse
import urllib.error

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/plain,*/*",
}


def test_url(name, url, parse_json=False):
    print(f"\n--- {name} ---")

    request = urllib.request.Request(
        url,
        headers=HEADERS,
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()

            print("HTTP status:", response.status)
            print("Content-Type:", response.headers.get("Content-Type"))
            print("Bytes received:", len(body))

            if parse_json:
                data = json.loads(body.decode("utf-8-sig"))

                if isinstance(data, dict):
                    print("JSON keys:", list(data.keys()))
                    for key, value in data.items():
                        if isinstance(value, list):
                            print("List:", key, "Count:", len(value))
                elif isinstance(data, list):
                    print("JSON list length:", len(data))

                return data

            print(
                "Preview:",
                body[:250].decode("utf-8", errors="replace")
            )

    except Exception as exc:
        print("FAILED:", repr(exc))

    return None


def main():
    search_url = (
        "https://cdn.tsetmc.com/api/Instrument/GetInstrumentSearch/"
        + urllib.parse.quote("فولاد", safe="")
    )

    search_data = test_url(
        "TSETMC instrument search",
        search_url,
        parse_json=True,
    )

    ins_code = "46348559193224090"

    if isinstance(search_data, dict):
        matches = search_data.get("instrumentSearch", [])

        if isinstance(matches, list):
            for item in matches:
                if item.get("lVal18AFC") == "فولاد":
                    ins_code = str(item.get("insCode", ins_code))
                    break

    print("\nInstrument code used:", ins_code)

    history_url = (
        "https://cdn.tsetmc.com/api/ClosingPrice/"
        f"GetClosingPriceDailyList/{ins_code}/0"
    )

    history_data = test_url(
        "TSETMC daily history",
        history_url,
        parse_json=True,
    )

    if isinstance(history_data, dict):
        rows = history_data.get("closingPriceDaily", [])

        if isinstance(rows, list):
            print("Historical candles:", len(rows))

            if rows:
                print("First record:")
                print(json.dumps(
                    rows[0],
                    ensure_ascii=False,
                    indent=2,
                ))

    old_url = (
        "http://old.tsetmc.com/tsev2/data/"
        f"InstTradeHistory.aspx?i={ins_code}&Top=100&A=0"
    )

    test_url(
        "TSETMC legacy history",
        old_url,
        parse_json=False,
    )

    print("\nChart source tests completed.")


if __name__ == "__main__":
    main()
