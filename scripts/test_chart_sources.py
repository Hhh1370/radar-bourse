import json
import os
from datetime import datetime, timezone
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


CHART_URL = "https://www.nahayatnegar.com/tv/IRO1KSHJ0001"

# هر دو شناسه را بررسی می‌کنیم؛ شناسهٔ دوم در درخواست قبلی چارت دیده شد.
SYMBOL_CANDIDATES = [
    "IRO1KSHJ00013",
    "IRO1KSHJ0001",
]

RESOLUTIONS = ["1D", "W", "M"]
LONG_TERM_START = "2018-01-01"
COUNTBACK_LONG_TERM = 5000

# پارامترهای درخواست تاریخچه‌ای که در اجرای قبلی موفق بود.
KNOWN_FROM = 1755475200
KNOWN_TO = 1791849600
KNOWN_COUNTBACK = 301


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
    value = datetime.strptime(date_text, "%Y-%m-%d")
    value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


BROWSER_TEST = r"""
const candidates = arguments[0];
const resolutions = arguments[1];
const longFrom = arguments[2];
const longTo = arguments[3];
const longCountback = arguments[4];
const knownFrom = arguments[5];
const knownTo = arguments[6];
const knownCountback = arguments[7];
const done = arguments[arguments.length - 1];

(async function () {
    const result = {
        pageUrl: location.href,
        config: null,
        symbolLookups: [],
        selectedSymbol: null,
        historyTests: [],
        errors: []
    };

    async function requestJson(path) {
        const response = await fetch(path, {
            method: "GET",
            credentials: "same-origin",
            headers: {"Accept": "application/json"}
        });

        const text = await response.text();
        let data;

        try {
            data = JSON.parse(text);
        } catch (error) {
            return {
                httpStatus: response.status,
                parseError: String(error),
                bodyPreview: text.slice(0, 500)
            };
        }

        return {
            httpStatus: response.status,
            data: data
        };
    }

    function getSymbolObject(data) {
        if (!data || typeof data !== "object") return null;

        if (data.tvSymbol && typeof data.tvSymbol === "object") {
            return data.tvSymbol;
        }

        if (data.symbol && typeof data.symbol === "object") {
            return data.symbol;
        }

        if (data.id || data.ticker || data.full_name) {
            return data;
        }

        return null;
    }

    function summarizeSymbol(data) {
        const item = getSymbolObject(data);

        if (!item) {
            return {
                id: null,
                ticker: null,
                name: null,
                full_name: null,
                type: null,
                responseKeys:
                    data && typeof data === "object"
                    ? Object.keys(data)
                    : [],
                responsePreview: JSON.stringify(data).slice(0, 1000)
            };
        }

        return {
            id: item.id || null,
            ticker: item.ticker || null,
            name: item.name || item.symbol || null,
            full_name: item.full_name || null,
            type: item.type || null,
            exchange: item.exchange || null,
            hasIntraday: item.has_intraday,
            hasWeeklyAndMonthly: item.has_weekly_and_monthly,
            responseKeys: Object.keys(item)
        };
    }

    function arrayLengths(data) {
        const output = {};

        if (data && typeof data === "object") {
            for (const [key, value] of Object.entries(data)) {
                if (Array.isArray(value)) {
                    output[key] = value.length;
                }
            }
        }

        return output;
    }

    function arr(data, keys) {
        for (const key of keys) {
            if (data && Array.isArray(data[key])) {
                return data[key];
            }
        }
        return null;
    }

    function sampleCandles(data) {
        const t = arr(data, ["t", "time", "timestamp"]);
        const o = arr(data, ["o", "open"]);
        const h = arr(data, ["h", "high"]);
        const l = arr(data, ["l", "low"]);
        const c = arr(data, ["c", "close"]);
        const v = arr(data, ["v", "volume"]);

        if (!t && !o && !h && !l && !c && !v) return [];

        const count = Math.max(
            0,
            ...(t ? [t.length] : []),
            ...(o ? [o.length] : []),
            ...(h ? [h.length] : []),
            ...(l ? [l.length] : []),
            ...(c ? [c.length] : []),
            ...(v ? [v.length] : [])
        );

        const indexes = [...new Set([
            0,
            1,
            Math.max(0, count - 3),
            Math.max(0, count - 2),
            Math.max(0, count - 1)
        ])].filter(i => i < count);

        return indexes.map(i => ({
            index: i,
            time: t && t[i] !== undefined ? t[i] : null,
            open: o && o[i] !== undefined ? o[i] : null,
            high: h && h[i] !== undefined ? h[i] : null,
            low: l && l[i] !== undefined ? l[i] : null,
            close: c && c[i] !== undefined ? c[i] : null,
            volume: v && v[i] !== undefined ? v[i] : null
        }));
    }

    try {
        const configResponse = await requestJson("/tv/chart/config");

        result.config = {
            httpStatus: configResponse.httpStatus,
            supportedResolutions:
                configResponse.data &&
                configResponse.data.supported_resolutions || [],
            rawKeys:
                configResponse.data &&
                Object.keys(configResponse.data) || [],
            error: configResponse.parseError || null
        };

        let selected = null;

        for (const candidate of candidates) {
            const path = "/tv/chart/symbols?symbol=" +
                encodeURIComponent(candidate);

            try {
                const response = await requestJson(path);
                const raw = response.data;
                const summary = summarizeSymbol(raw);

                result.symbolLookups.push({
                    requestedSymbol: candidate,
                    httpStatus: response.httpStatus,
                    symbol: summary,
                    rawResponse: raw || response.bodyPreview || null
                });

                const item = getSymbolObject(raw);

                if (
                    response.httpStatus === 200 &&
                    item &&
                    (item.id || item.ticker)
                ) {
                    selected = item;
                    result.selectedSymbol = {
                        requestedSymbol: candidate,
                        id: item.id || null,
                        ticker: item.ticker || null,
                        name: item.name || item.symbol || null,
                        full_name: item.full_name || null,
                        type: item.type || "stock",
                        exchange: item.exchange || null
                    };
                    break;
                }
            } catch (error) {
                result.symbolLookups.push({
                    requestedSymbol: candidate,
                    error: String(error)
                });
            }
        }

        // فقط برای تشخیص خطا، اگر شناسایی نماد موفق نشد،
        // درخواست تاریخچه با شناسهٔ مشاهده‌شدهٔ قبلی هم انجام می‌شود.
        const historySymbol = selected
            ? (selected.id || selected.ticker)
            : "IRO1KSHJ00013";

        const symbolType = selected && selected.type
            ? selected.type
            : "stock";

        const tests = [{
            label: "known_previous_request",
            resolution: "1D",
            from: knownFrom,
            to: knownTo,
            countback: knownCountback
        }];

        for (const resolution of resolutions) {
            tests.push({
                label: "long_term",
                resolution: resolution,
                from: longFrom,
                to: longTo,
                countback: longCountback
            });
        }

        for (const test of tests) {
            const params = new URLSearchParams({
                symbol: historySymbol,
                resolution: test.resolution,
                from: String(test.from),
                to: String(test.to),
                type: symbolType,
                adjustmentType: "3",
                countback: String(test.countback)
            });

            const path = "/tv/chart/history?" + params.toString();

            try {
                const response = await requestJson(path);
                const data = response.data || {};

                result.historyTests.push({
                    label: test.label,
                    symbol: historySymbol,
                    resolution: test.resolution,
                    from: test.from,
                    to: test.to,
                    countback: test.countback,
                    httpStatus: response.httpStatus,
                    apiStatus: data.s || data.status || null,
                    errorMessage: data.errmsg || data.error || null,
                    responseKeys: Object.keys(data),
                    arrayLengths: arrayLengths(data),
                    samples: sampleCandles(data),
                    rawResponsePreview:
                        JSON.stringify(data).slice(0, 1000),
                    parseError: response.parseError || null
                });
            } catch (error) {
                result.historyTests.push({
                    label: test.label,
                    resolution: test.resolution,
                    error: String(error)
                });
            }
        }
    } catch (error) {
        result.errors.push(String(error));
    }

    done(result);
})().catch(error => done({
    fatalError: String(error)
}));
"""


def main():
    now = int(datetime.now(timezone.utc).timestamp())
    start = utc_timestamp(LONG_TERM_START)

    # کمی جلوتر از زمان فعلی برای پوشش آخرین کندل احتمالی
    end = now + 86400

    print("=" * 20 + " TEST SETTINGS " + "=" * 20)
    print("Chart URL:", CHART_URL)
    print("Symbol candidates:", ", ".join(SYMBOL_CANDIDATES))
    print("Long-term start:", LONG_TERM_START)
    print("Resolutions:", ", ".join(RESOLUTIONS))

    driver = None

    try:
        driver = make_driver()
        print("\nOpening chart...")
        driver.get(CHART_URL)

        result = driver.execute_async_script(
            BROWSER_TEST,
            SYMBOL_CANDIDATES,
            RESOLUTIONS,
            start,
            end,
            COUNTBACK_LONG_TERM,
            KNOWN_FROM,
            KNOWN_TO,
            KNOWN_COUNTBACK
        )

        print("\n" + "=" * 20 + " FULL API RESULTS " + "=" * 20)
        print(json.dumps(result, ensure_ascii=False, indent=2))

        print("\n" + "=" * 20 + " QUICK SUMMARY " + "=" * 20)

        selected = result.get("selectedSymbol")
        print(
            "Selected symbol:",
            json.dumps(selected, ensure_ascii=False)
        )

        for lookup in result.get("symbolLookups", []):
            print(
                "Symbol lookup:",
                lookup.get("requestedSymbol"),
                "| HTTP:",
                lookup.get("httpStatus"),
                "| ID:",
                (lookup.get("symbol") or {}).get("id"),
                "| Name:",
                (lookup.get("symbol") or {}).get("name"),
                "| Error:",
                lookup.get("error", "")
            )

        for test in result.get("historyTests", []):
            print(
                "Test:",
                test.get("label"),
                "| Resolution:",
                test.get("resolution"),
                "| HTTP:",
                test.get("httpStatus"),
                "| API status:",
                test.get("apiStatus"),
                "| Arrays:",
                json.dumps(
                    test.get("arrayLengths", {}),
                    ensure_ascii=False
                ),
                "| Error:",
                test.get("errorMessage") or test.get("error") or ""
            )

        if result.get("errors"):
            print("Errors:", json.dumps(
                result["errors"],
                ensure_ascii=False
            ))

        if result.get("fatalError"):
            print("Fatal:", result["fatalError"])

    except Exception as error:
        print("TEST FAILED:", repr(error))
        raise

    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    main()
