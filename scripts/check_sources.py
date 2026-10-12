import re
import urllib.request
import urllib.parse

SOURCES = {
    "TradersArena": "https://tradersarena.ir/",
    "BourseTrader_Codal": (
        "https://bourse-trader.ir/index.php?task=codal-reports"
    ),
}

def fetch(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        return response.read(500000).decode("utf-8", errors="replace")

def inspect(name, page_url):
    print(f"\n--- {name} ---")

    try:
        html = fetch(page_url)
        scripts = re.findall(
            r'<script\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )

        print("Page received: YES")
        print("Script files found:", len(scripts))

        base_host = urllib.parse.urlparse(page_url).netloc
        checked = 0

        for src in scripts:
            js_url = urllib.parse.urljoin(page_url, src)
            parsed = urllib.parse.urlparse(js_url)

            # فقط فایل‌های همان سایت بررسی می‌شوند.
            if parsed.netloc != base_host:
                continue

            try:
                js = fetch(js_url)
                checked += 1
                print("\nSCRIPT:", js_url)

                patterns = [
                    r"""["'`]([^"'`\s]{1,160}(?:/api/|api/|\.json|ajax|codal)[^"'`\s]{0,100})["'`]""",
                    r"""(?:fetch|axios|ajax)\s*\(\s*["'`]([^"'`]{1,200})["'`]""",
                ]

                found = set()
                for pattern in patterns:
                    found.update(re.findall(pattern, js, re.IGNORECASE))

                for item in sorted(found)[:12]:
                    print("  CANDIDATE:", item)

                if checked >= 10:
                    print("\nReached the 10-script inspection limit.")
                    break

            except Exception as exc:
                print("SCRIPT FAILED:", type(exc).__name__)

    except Exception as exc:
        print("PAGE FAILED:", repr(exc))

if __name__ == "__main__":
    for name, url in SOURCES.items():
        inspect(name, url)

    print("\nInspection completed.")
