import os
import json
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
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
            lambda d: len(d.find_elements(By.CLASS_NAME, "leaf")) > 0
        )
        time.sleep(4)

        leaves = driver.find_elements(By.CLASS_NAME, "leaf")
        empty_indexes = [
            i for i, el in enumerate(leaves)
            if not el.text.strip()
        ]

        print("Total tiles:", len(leaves))
        print("Tiles without visible text:", len(empty_indexes))
        print("\n--- HOVER TEST ---")

        for index in empty_indexes[:20]:
            # پیدا کردن دوبارهٔ عنصر، برای جلوگیری از خطای DOM قدیمی
            current_leaves = driver.find_elements(
                By.CLASS_NAME, "leaf"
            )
            if index >= len(current_leaves):
                continue

            element = current_leaves[index]

            details = {
                "index": index,
                "before_text": element.text.strip(),
                "title": element.get_attribute("title"),
                "aria_label": element.get_attribute("aria-label"),
                "data_name": element.get_attribute("data-name"),
                "html_before": (
                    element.get_attribute("outerHTML") or ""
                )[:300],
            }

            try:
                ActionChains(driver).move_to_element(
                    element
                ).pause(0.5).perform()
                time.sleep(0.3)

                details["after_text"] = element.text.strip()
                details["title_after"] = element.get_attribute("title")

                # جست‌وجوی پیام‌های راهنما یا پنجره‌های شناور
                tooltip_nodes = driver.find_elements(
                    By.CSS_SELECTOR,
                    "[role='tooltip'], "
                    "[class*='tooltip'], "
                    "[class*='Tooltip'], "
                    "[class*='popover'], "
                    "[class*='Popover']"
                )

                tooltips = []
                for node in tooltip_nodes:
                    if node.is_displayed():
                        text = node.text.strip()
                        if text:
                            tooltips.append({
                                "text": text[:200],
                                "class": node.get_attribute("class"),
                            })

                details["tooltips"] = tooltips[:5]

            except Exception as exc:
                details["hover_error"] = repr(exc)

            print(json.dumps(
                details,
                ensure_ascii=False,
            ))

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
