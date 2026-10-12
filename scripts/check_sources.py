import json
import os
import re
import time
from datetime import datetime, timezone

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


URL = "https://rahavard365.com/marketmap"

DIGIT_MAP = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789"
)

PERCENT_PATTERN = re.compile(
    r"([+-]?\s*\d+(?:[.,]\d+)?\s*[%٪])"
)


def normalize_text(text):
    return (
        text.translate(DIGIT_MAP)
        .replace("٫", ".")
        .replace("−", "-")
        .replace("٪", "%")
    )


def parse_change(text):
    normalized = normalize_text(text)
    match = PERCENT_PATTERN.search(normalized)

    if not match:
        return None, None

    display = re.sub(r"\s+", "", match.group(1))
    number = display.replace("%", "").replace(",", ".")

    try:
        return float(number), display
    except ValueError:
        return None, display


def main():
    options = Options()
    options.binary_location = os.environ["CHROME_BIN"]
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=fa-IR")

    service = Service(os.environ["CHROMEDRIVER"])
    driver = webdriver.Chrome(service=service, options=options)

    try:
        driver.set_page_load_timeout(45)
        print("Opening Rahavard market map...")
        driver.get(URL)

        WebDriverWait(driver, 30).until(
            lambda browser: len(
                browser.find_elements(By.CLASS_NAME, "leaf")
            ) > 0
        )

        time.sleep(3)

        print("Page title:", driver.title)
        print("Final URL:", driver.current_url)

        elements = driver.find_elements(By.CLASS_NAME, "leaf")
        print("Leaf elements found:", len(elements))

        items = {}

        for element in elements:
            text = element.text.strip()
            if not text:
                continue

            lines = [
                line.strip()
                for line in text.splitlines()
                if line.strip()
            ]

            if not lines:
                continue

            symbol = lines[0]

            # ردکردن متن‌های خالی یا غیرمرتبط
            if len(symbol) > 30:
                continue

            if not any(char.isalpha() for char in symbol):
                continue

            change, display_change = parse_change(text)

            if symbol not in items or (
                items[symbol]["change_pct"] is None
                and change is not None
            ):
                items[symbol] = {
                    "symbol": symbol,
                    "change_pct": change,
                    "display_change": display_change,
                    "raw_text": text,
                }

        if not items:
            raise RuntimeError(
                "No stock items could be extracted from the market map."
            )

        os.makedirs("data", exist_ok=True)

        payload = {
            "source": "Rahavard365 market map",
            "source_url": driver.current_url,
            "captured_at_utc": datetime.now(
                timezone.utc
            ).isoformat(),
            "count": len(items),
            "items": list(items.values()),
        }

        output_path = "data/marketmap.json"

        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2,
            )

        print("Extracted unique items:", len(items))
        print("Items with change percentage:", sum(
            item["change_pct"] is not None
            for item in items.values()
        ))
        print("JSON saved to:", output_path)
        print("Sample:")
        print(json.dumps(
            list(items.values())[:10],
            ensure_ascii=False,
            indent=2,
        ))

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
