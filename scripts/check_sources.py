import json
import os
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By


URL = "https://rahavard365.com/marketmap"


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

        time.sleep(10)

        print("Page title:", driver.title)
        print("Final URL:", driver.current_url)
        print("Page source length:", len(driver.page_source))

        selectors = [
            ".leaf",
            "[data-name]",
            "[data-display-color]",
            "table tbody tr",
            "[class*='symbol']",
            "[class*='market']",
        ]

        print("\n--- ELEMENT COUNTS ---")
        for selector in selectors:
            try:
                count = len(driver.find_elements(By.CSS_SELECTOR, selector))
                print(f"{selector}: {count}")
            except Exception as exc:
                print(f"{selector}: ERROR - {exc}")

        elements = driver.find_elements(
            By.CSS_SELECTOR,
            ".leaf, [data-name]"
        )

        rows = []
        seen = set()

        for element in elements:
            name = element.get_attribute("data-name")
            value = element.get_attribute("data-display-color")
            text = element.text.strip()

            if not name:
                name = text

            if name:
                name = name.strip()
                if name and name not in seen:
                    seen.add(name)
                    rows.append({
                        "name": name,
                        "display_value": value,
                        "text": text[:150],
                    })

        print("\nExtracted items:", len(rows))
        print("Sample data:")
        print(json.dumps(rows[:15], ensure_ascii=False, indent=2))

        if not rows:
            print("\nNo stock items found with the current selectors.")
            print("Visible page text:")
            print(driver.find_element(By.TAG_NAME, "body").text[:2500])

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
