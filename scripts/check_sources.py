import os
import time
from collections import Counter

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

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
        driver.get(URL)

        WebDriverWait(driver, 30).until(
            lambda d: len(
                d.find_elements(By.CLASS_NAME, "leaf")
            ) > 0
        )
        time.sleep(5)

        leaves = driver.find_elements(By.CLASS_NAME, "leaf")

        visible = []
        hidden = []

        for element in leaves:
            text = element.text.strip()
            record = {
                "text": text,
                "html": element.get_attribute("outerHTML")[:500],
            }

            if not text:
                continue

            if element.is_displayed():
                visible.append(record)
            else:
                hidden.append(record)

        def symbol_from(record):
            lines = [
                line.strip()
                for line in record["text"].splitlines()
                if line.strip()
            ]
            return lines[0] if lines else ""

        visible_symbols = {
            symbol_from(x) for x in visible if symbol_from(x)
        }
        all_symbols = {
            symbol_from(x)
            for x in visible + hidden
            if symbol_from(x)
        }

        print("Page title:", driver.title)
        print("Page URL:", driver.current_url)
        print("All leaf elements:", len(leaves))
        print("Visible non-empty leaves:", len(visible))
        print("Hidden non-empty leaves:", len(hidden))
        print("Unique visible labels:", len(visible_symbols))
        print("Unique all labels:", len(all_symbols))

        print("\n--- SELECT FILTERS ---")
        for select in driver.find_elements(By.TAG_NAME, "select"):
            options_text = [
                option.text.strip()
                for option in select.find_elements(
                    By.TAG_NAME, "option"
                )
                if option.text.strip()
            ]
            print({
                "id": select.get_attribute("id"),
                "name": select.get_attribute("name"),
                "options": options_text[:40],
            })

        print("\n--- BUTTONS ---")
        buttons = []
        for element in driver.find_elements(
            By.CSS_SELECTOR, "button, [role='button']"
        ):
            text = element.text.strip()
            if text and text not in buttons:
                buttons.append(text)

        for text in buttons[:60]:
            print(text)

        print("\n--- SAMPLE VISIBLE ITEMS ---")
        for record in visible[:8]:
            print("TEXT:", repr(record["text"]))
            print("HTML:", record["html"])

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
