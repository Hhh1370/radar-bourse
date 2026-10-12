import os
import json
import time

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

    driver = webdriver.Chrome(
        service=Service(os.environ["CHROMEDRIVER"]),
        options=options,
    )

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

        labeled = []
        blank_samples = []
        blank_count = 0
        text_content_count = 0
        child_label_count = 0

        for element in leaves:
            text = element.text.strip()

            text_content = driver.execute_script(
                "return arguments[0].textContent || '';",
                element,
            ).strip()

            label_elements = element.find_elements(
                By.CSS_SELECTOR, ".text"
            )

            child_texts = []
            for label in label_elements:
                child_text = driver.execute_script(
                    "return arguments[0].textContent || '';",
                    label,
                ).strip()
                if child_text:
                    child_texts.append(child_text)

            if text_content:
                text_content_count += 1

            if child_texts:
                child_label_count += 1

            if text:
                labeled.append(text)

            if not text_content:
                blank_count += 1

                if len(blank_samples) < 12:
                    size = driver.execute_script("""
                        const r = arguments[0].getBoundingClientRect();
                        return {
                            width: Math.round(r.width),
                            height: Math.round(r.height)
                        };
                    """, element)

                    blank_samples.append({
                        "size": size,
                        "class": element.get_attribute("class"),
                        "style": element.get_attribute("style"),
                        "innerHTML": (
                            element.get_attribute("innerHTML") or ""
                        )[:500],
                        "child_labels": child_texts,
                    })

        print("Page title:", driver.title)
        print("Total leaf elements:", len(leaves))
        print("Elements with visible text:", len(labeled))
        print("Elements with textContent:", text_content_count)
        print("Elements with .text child labels:", child_label_count)
        print("Elements with empty textContent:", blank_count)

        print("\n--- FIRST 12 EMPTY ELEMENTS ---")
        print(json.dumps(
            blank_samples,
            ensure_ascii=False,
            indent=2,
        ))

        print("\n--- FIRST 15 LABELED ELEMENTS ---")
        for text in labeled[:15]:
            print(repr(text))

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
