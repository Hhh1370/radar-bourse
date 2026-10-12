
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


# صفحه‌ای که چارت نماد در آن باز می‌شود
CHART_URL = "https://www.nahayatnegar.com/tv/IRO1KSHJ0001"

# شناسه‌ای که از صفحهٔ چارت دریافت می‌شود
INPUT_SYMBOL = "IRO1KSHJ0001"

# شروع تاریخچه برای آزمایش افق بلندمدت
START_DATE = "2018-01-01"

# بازه‌های زمانی مورد آزمایش
RESOLUTIONS = ["1D", "W", "M"]

# حداکثر تعداد کندل درخواستی؛ تعداد واقعی در خروجی مشخص می‌شود
COUNTBACK = 5000


def make_driver():
    options = Options()

    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin and Path(chrome_bin).exists():
        options.binary_location = chrome_bin

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1440,1000")
    options.add_argument("--lang=fa-IR")

    driver_path = os.environ.get("CHROMEDRIVER")

    if driver_path and Path(driver_path).is_file():
        service = Service(executable_path=driver_path)
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(60)
    driver.set_script_timeout(180)
    return driver


def utc_timestamp(date_text):
    date_value = datetime.strptime(date_text, "%Y-%m-%d")
    date_value = date_value.replace(tzinfo=timezone.utc)
    return int(date_value.timestamp())


# این کد داخل مرورگر اجرا می‌شود تا درخواست‌ها از همان مبدأ
# و با همان شرایط دسترسی صفحهٔ چارت ارسال شوند.
BROWSER_TEST = r"""
const inputSymbol = arguments[0];
const resolutions = arguments[1];
const fromTime = arguments[2];
const toTime = arguments[3];
const countback = arguments[4];
const done = arguments[arguments.length - 1];

(async function () {
    const output = {
        pageUrl: location.href,
        config: null,
        symbol: null,
        history: [],
        errors: []
    };

    async function readJson(url) {
        const response = await fetch(url, {
            method: "GET",
            credentials: "same-origin",
            headers: {"Accept": "application/json"}
        });

        const text = await response.text();

        let data;
        try {
            data = JSON.parse(text);
        } catch (error) {
            throw new Error(
                "پاسخ JSON معتبر نیست؛ HTTP " +
                response.status + "؛ " + text.slice(0, 300)
            );
        }

        return {
            httpStatus: response.status,
            data: data
        };
    }

    function getArrayLengths(data) {
        const result = {};

        if (data && typeof data === "object") {
            for (const [key, value] of Object.entries(data)) {
                if (Array.isArray(value)) {
                    result[key] = value.length;
                }
            }
        }

        return result;
    }

    function firstArray(data, names) {
        for (const name of names) {
            if (data && Array.isArray(data[name])) {
                return data[name];
            }
        }
        return null;
    }

    function makeSamples(data) {
        const times = firstArray(data, ["t", "time", "timestamp"]);
        const opens = firstArray(data, ["o", "open"]);
        const highs = firstArray(data, ["h", "high"]);
        const lows = firstArray(data, ["l", "low"]);
        const closes = firstArray(data, ["c", "close"]);
        const volumes = firstArray(data, ["v", "volume"]);

        const arrays = [
            times, opens, highs, lows, closes, volumes
        ].filter(Array.isArray);

        const count = arrays.length
            ? Math.max(...arrays.map(a => a.length))
            : 0;

        const indices = [...new Set([
            0,
            1,
            Math.max(0, count - 5),
            Math.max(0, count - 4),
            Math.max(0, count - 3),
            Math.max(0, count - 2),
            Math.max(0, count - 1)
        ])].filter(i => i < count);

        return indices.map(i => {
            function value(array) {
                return Array.isArray(array) && i < array.length
                    ? array[i]
                    : null;
            }

            return {
                index: i,
                time: value(times),
                open: value(opens),
                high: value(highs),
                low: value(lows),
                close: value(closes),
                volume: value(volumes)
            };
        });
    }

    try {
        const configResult = await readJson("/tv/chart/config");
        output.config = {
            httpStatus: configResult.httpStatus,
            supportedResolutions:
                configResult.data.supported_resolutions || [],
            supportsSearch: configResult.data.supports_search,
            supportsMarks: configResult.data.supports_marks
        };

        const symbolUrl = "/tv/chart/symbols?symbol=" +
            encodeURIComponent(inputSymbol);

        const symbolResult = await readJson(symbolUrl);
        const symbolData = symbolResult.data.tvSymbol ||
            symbolResult.data;

        output.symbol = {
            httpStatus: symbolResult.httpStatus,
            id: symbolData.id || null,
            ticker: symbolData.ticker || null,
            name: symbolData.name || null,
            full_name: symbolData.full_name || null,
            type: symbolData.type || null,
            exchange: symbolData.exchange || null,
            hasIntraday: symbolData.has_intraday,
            hasWeeklyAndMonthly: symbolData.has_weekly_and_monthly
        };

        const canonicalSymbol =
            symbolData.id || symbolData.ticker || inputSymbol;

        for (const resolution of resolutions) {
            const params = new URLSearchParams({
                symbol: canonicalSymbol,
                resolution: resolution,
                from: String(fromTime),
                to: String(toTime),
                type: symbolData.type || "stock",
                adjustmentType: "3",
                countback: String(countback)
            });

            const historyUrl =
                "/tv/chart/history?" + params.toString();

            try {
                const response = await readJson(historyUrl);
                const body = response.data || {};

                const samples = makeSamples(body);

                output.history.push({
                    resolution: resolution,
                    httpStatus: response.httpStatus,
                    apiStatus: body.s || body.status || null,
                    message: body.errmsg || body.error || null,
                    arrayLengths: getArrayLengths(body),
                    samples: samples,
                    responseKeys: Object.keys(body)
                });
            } catch (error) {
                output.history.push({
                    resolution: resolution,
                    error: String(error)
                });
            }
        }
    } catch (error) {
        output.errors.push(String(error));
    }

    done(output);
})().catch(error => done({
    fatalError: String(error)
}));
"""


def main():
    driver = None

    start_timestamp = utc_timestamp(START_DATE)
    end_timestamp = int(datetime.now(timezone.utc).timestamp()) + 86400

    print("=" * 18 + " TEST SETTINGS " + "=" * 18)
    print("Chart URL:", CHART_URL)
    print("Input symbol:", INPUT_SYMBOL)
    print("Start date UTC:", START_DATE)
    print(
        "End date UTC:",
        datetime.fromtimestamp(
            end_timestamp,
            timezone.utc
        ).strftime("%Y-%m-%d")
    )
    print("Resolutions:", ", ".join(RESOLUTIONS))
    print("Countback:", COUNTBACK)

    try:
        driver = make_driver()

        print("\nOpening chart page...")
        driver.get(CHART_URL)

        # اجازه می‌دهیم صفحه و ابزار چارت بارگذاری شوند.
        driver.implicitly_wait(2)

        result = driver.execute_async_script(
            BROWSER_TEST,
            INPUT_SYMBOL,
            RESOLUTIONS,
            start_timestamp,
            end_timestamp,
            COUNTBACK
        )

        print("\n" + "=" * 18 + " API RESULTS " + "=" * 18)
        print(json.dumps(
            result,
            ensure_ascii=False,
            indent=2
        ))

        # نتیجهٔ خلاصه برای دیدن سریع موفقیت یا شکست
        print("\n" + "=" * 18 + " QUICK SUMMARY " + "=" * 18)

        symbol = result.get("symbol") or {}
        print("Symbol HTTP:", symbol.get("httpStatus"))
        print("Resolved ID:", symbol.get("id"))
        print("Resolved name:", symbol.get("name"))

        history = result.get("history", [])
        for item in history:
            print(
                "Resolution:",
                item.get("resolution"),
                "| HTTP:",
                item.get("httpStatus"),
                "| API status:",
                item.get("apiStatus"),
                "| Array lengths:",
                json.dumps(
                    item.get("arrayLengths", {}),
                    ensure_ascii=False
                ),
                "| Error:",
                item.get("error", "")
            )

        if result.get("errors") or result.get("fatalError"):
            print("Errors:", json.dumps(
                result.get("errors", []),
                ensure_ascii=False
            ))
            print("Fatal:", result.get("fatalError", ""))

    except Exception as error:
        print("TEST FAILED:", repr(error))
        raise

    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    main()
