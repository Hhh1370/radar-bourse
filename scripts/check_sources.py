import json
import time
import urllib.request
import urllib.error
import socket

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

def check_source(name, url, attempts=3):
    print(f"\n--- {name} ---")

    for attempt in range(1, attempts + 1):
        print(f"Attempt {attempt}/{attempts}")

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; RadarBourse/1.0)",
                "Accept": "application/json, */*",
                "Connection": "close",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=45) as response:
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

                return True

        except (TimeoutError, socket.timeout) as exc:
            print("TIMEOUT:", repr(exc))
        except urllib.error.HTTPError as exc:
            print("HTTP ERROR:", exc.code, exc.reason)
            if exc.code not in (408, 429, 500, 502, 503, 504):
                break
        except urllib.error.URLError as exc:
            print("CONNECTION ERROR:", repr(exc.reason))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            print("INVALID RESPONSE:", repr(exc))
            break
        except Exception as exc:
            print("OTHER ERROR:", repr(exc))
            break

        if attempt < attempts:
            time.sleep(3)

    print(f"RESULT: {name} unavailable from this runner")
    return False


if __name__ == "__main__":
    results = {
        name: check_source(name, url)
        for name, url in SOURCES.items()
    }

    print("\n--- FINAL RESULT ---")
    for name, ok in results.items():
        print(f"{name}: {'OK' if ok else 'FAILED'}")

    if not all(results.values()):
        raise SystemExit(1)

