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


def dismiss_tour(driver):
    print("\n--- DISMISSING RAHAVARD GUIDE ---")

    for step in range(15):
        tips = [
            tip for tip in driver.find_elements(
                By.CSS_SELECTOR, ".react-joyride__tooltip"
            )
            if tip.is_displayed()
        ]

        if not tips:
            print("Guide closed.")
            return

        buttons = tips[0].find_elements(
            By.CSS_SELECTOR, "button, [role='button']"
        )

        if not buttons:
            print("Guide has no clickable button.")
            return

        button = buttons[0]

        try:
            print("Guide button:", button.text.strip())
            button.click()
        except Exception:
            driver.execute_script("arguments[0].click();", button)

        time.sleep(0.7)

    print("Guide dismissal limit reached.")


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

        time.sleep(3)
        dismiss_tour(driver)
        time.sleep(2)

        leaves = driver.find_elements(By.CLASS_NAME, "leaf")
        candidates = []

        for element in leaves:
            if element.text.strip():
                continue

            size = driver.execute_script("""
                const r = arguments[0].getBoundingClientRect();
                return {
                    width: Math.round(r.width),
                    height: Math.round(r.height),
                    area: r.width * r.height
                };
            """, element)

            candidates.append((size["area"], size, element))

        # ابتدا خانه‌های خالی بزرگ‌تر آزمایش می‌شوند.
        candidates.sort(key=lambda x: x[0], reverse=True)

        print("\n--- HOVER TEST AFTER GUIDE DISMISSAL ---")
        print("Total tiles:", len(leaves))
        print("Empty tiles:", len(candidates))
        print("Testing largest 15 empty tiles.")

        for index, (_, size, element) in enumerate(candidates[:15]):
            result = {
                "sample": index + 1,
                "size": size,
                "before_html": (
                    element.get_attribute("outerHTML") or ""
                )[:350],
            }

            try:
                ActionChains(driver).move_to_element(
                    element
                ).pause(1).perform()

                time.sleep(0.4)

                result["after_text"] = element.text.strip()
                result["after_html"] = (
                    element.get_attribute("outerHTML") or ""
                )[:500]

                result["title"] = element.get_attribute("title")
                result["aria_label"] = element.get_attribute("aria-label")

                tooltip_texts = []
                selectors = [
                    "[role='tooltip']",
                    "[class*='tooltip']:not(.react-joyride__tooltip)",
                    "[class*='popover']",
                ]

                for selector in selectors:
                    for node in driver.find_elements(
                        By.CSS_SELECTOR, selector
                    ):
                        if node.is_displayed():
                            text = node.text.strip()
                            if text and text not in tooltip_texts:
                                tooltip_texts.append(text[:250])

                result["tooltips"] = tooltip_texts[:5]

            except Exception as exc:
                result["error"] = repr(exc)

            print(json.dumps(result, ensure_ascii=False))

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
