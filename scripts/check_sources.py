import re
import json
import urllib.request
import urllib.parse

BASE = "https://rahavard365.com"
HEADERS = {"User-Agent": "Mozilla/5.0"}

def fetch(url):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")

def show_context(source, term, limit=8, radius=350):
    positions = []
    start = 0

    while True:
        pos = source.lower().find(term.lower(), start)
        if pos < 0:
            break
        positions.append(pos)
        start = pos + len(term)

    print(f"\nTERM: {term} | occurrences: {len(positions)}")

    for pos in positions[:limit]:
        snippet = source[max(0, pos-radius):pos+radius]
        print("---")
        print(snippet)

def main():
    print("Fetching Rahavard page...")
    html = fetch(BASE + "/marketmap")

    scripts = re.findall(
        r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )

    chunks = [
        urllib.parse.urljoin(BASE, src)
        for src in scripts
        if "pages/marketmap-" in src
    ]

    if not chunks:
        raise RuntimeError("Market-map JavaScript chunk not found.")

    js_url = chunks[0]
    js = fetch(js_url)

    print("Chunk URL:", js_url)
    print("JavaScript bytes:", len(js.encode("utf-8")))

    terms = [
        "/api/v2/marketmap/data",
        "/api/v2/marketmap/filters",
        "asset_id",
        "exchange_id",
        "exchangeId",
        "exchanges",
        "category_id",
        "industry_id",
        "market_id",
        "marketmap/data",
    ]

    for term in terms:
        show_context(js, term)

    print("\n--- FILTER OPTIONS ---")
    filters = json.loads(fetch(BASE + "/api/v2/marketmap/filters"))
    print(json.dumps(
        filters.get("data", {}),
        ensure_ascii=False,
        indent=2,
    )[:12000])

    print("\nInspection completed.")

if __name__ == "__main__":
    main()
