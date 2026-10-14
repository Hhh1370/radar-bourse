#!/usr/bin/env python3
"""Walk-forward validation of the technical radar against historical daily prices.

This is a strict as-of-date re-score of the existing ranking model, not a strategy
optimized on historical outcomes. It uses only the current symbol universe, so its
results are explicitly subject to survivorship bias. Returns use next-session open
as entry and the Nth future session close as exit; costs/dividends are excluded.
"""
from __future__ import annotations

import bisect
import json
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_FILE = ROOT / "data" / "stock_universe.json"
OUTPUT_FILE = ROOT / "data" / "radar_validation.json"
HORIZONS = (20, 60, 120)
# Fixed half-year evaluation dates. The last fold leaves enough history for 120 sessions.
CUTOFF_DATES = tuple(
    date(year, month, day)
    for year in range(2020, 2026)
    for month, day in ((6, 30), (12, 31))
)
CUTOFF_DATES = tuple(d for d in CUTOFF_DATES if d <= date(2025, 12, 31))
WEEKLY_COMPLETION_LAG_DAYS = 7
MONTHLY_COMPLETION_LAG_DAYS = 32
MIN_DAILY_BARS = 200
MIN_WEEKLY_BARS = 26
MIN_MONTHLY_BARS = 12
MAX_DAILY_STALENESS_DAYS = 10


def _technical_module():
    try:
        from scripts import build_technical_radar as module
    except ModuleNotFoundError:  # direct execution from scripts/
        import build_technical_radar as module
    return module


def clean_candles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only finite, geometrically valid OHLCV rows while preserving timestamps."""
    output = []
    seen: dict[int, dict[str, Any]] = {}
    for row in rows or []:
        try:
            timestamp = int(row["time"])
            o, h, low, close = (float(row[key]) for key in ("open", "high", "low", "close"))
            if not all(math.isfinite(v) and v > 0 for v in (o, h, low, close)):
                continue
            if h < max(o, close, low) or low > min(o, close, h):
                continue
            raw_volume = row.get("volume")
            volume = float(raw_volume) if raw_volume is not None else None
            if volume is not None and (not math.isfinite(volume) or volume < 0):
                volume = None
            seen[timestamp] = {
                "time": timestamp, "open": o, "high": h, "low": low,
                "close": close, "volume": volume,
            }
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
    output = [seen[key] for key in sorted(seen)]
    return output


def candle_dates(rows: list[dict[str, Any]]) -> list[date]:
    return [
        datetime.fromtimestamp(int(row["time"]), timezone.utc).date()
        for row in rows
    ]


def last_index_on_or_before(dates: list[date], cutoff: date) -> int:
    return bisect.bisect_right(dates, cutoff) - 1


def forward_return_pct(
    daily: list[dict[str, Any]],
    daily_dates: list[date],
    cutoff: date,
    horizon: int,
) -> float | None:
    """Return from the next session open through the Nth session close after cutoff."""
    index = last_index_on_or_before(daily_dates, cutoff)
    entry_index = index + 1
    exit_index = index + horizon
    if index < 0 or entry_index >= len(daily) or exit_index >= len(daily):
        return None
    entry = daily[entry_index].get("open")
    exit_price = daily[exit_index].get("close")
    if not (
        isinstance(entry, (int, float)) and math.isfinite(float(entry)) and entry > 0
        and isinstance(exit_price, (int, float)) and math.isfinite(float(exit_price)) and exit_price > 0
    ):
        return None
    value = (float(exit_price) / float(entry) - 1.0) * 100.0
    return round(value, 5) if math.isfinite(value) else None


def _cut_series(
    rows: list[dict[str, Any]], dates: list[date], cutoff: date, lag_days: int = 0
) -> list[dict[str, Any]]:
    index = last_index_on_or_before(dates, cutoff - timedelta(days=lag_days))
    return rows[:index + 1] if index >= 0 else []


def score_as_of(stock: dict[str, Any], daily: list[dict[str, Any]],
                weekly: list[dict[str, Any]], monthly: list[dict[str, Any]],
                cutoff: date, tech: Any) -> dict[str, Any] | None:
    daily_dates, weekly_dates, monthly_dates = (
        candle_dates(daily), candle_dates(weekly), candle_dates(monthly)
    )
    d = _cut_series(daily, daily_dates, cutoff)
    w = _cut_series(weekly, weekly_dates, cutoff, WEEKLY_COMPLETION_LAG_DAYS)
    m = _cut_series(monthly, monthly_dates, cutoff, MONTHLY_COMPLETION_LAG_DAYS)
    if len(d) < MIN_DAILY_BARS or len(w) < MIN_WEEKLY_BARS or len(m) < MIN_MONTHLY_BARS:
        return None
    latest_date = candle_dates(d)[-1]
    data_age = (cutoff - latest_date).days
    if data_age < 0 or data_age > MAX_DAILY_STALENESS_DAYS:
        return None

    dc, wc, mc = ([float(row["close"]) for row in series] for series in (d, w, m))
    daily_sma50, daily_sma200 = tech.sma(dc, 50), tech.sma(dc, 200)
    weekly_sma13, weekly_sma26 = tech.sma(wc, 13), tech.sma(wc, 26)
    monthly_sma6, monthly_sma12 = tech.sma(mc, 6), tech.sma(mc, 12)
    daily20, daily60 = tech.pct_change(dc, 20), tech.pct_change(dc, 60)
    weekly13, monthly6 = tech.pct_change(wc, 13), tech.pct_change(mc, 6)
    rsi_value = tech.rsi(dc)

    def above(left: Any, right: Any) -> bool | None:
        return None if left is None or right is None else left > right

    checks = {
        "daily_close_above_sma50": above(dc[-1], daily_sma50),
        "daily_sma50_above_sma200": above(daily_sma50, daily_sma200),
        "weekly_close_above_sma13": above(wc[-1], weekly_sma13),
        "weekly_sma13_above_sma26": above(weekly_sma13, weekly_sma26),
        "monthly_close_above_sma6": above(mc[-1], monthly_sma6),
        "monthly_sma6_above_sma12": above(monthly_sma6, monthly_sma12),
        "weekly_13_period_return_positive": None if weekly13 is None else weekly13 > 0,
        "monthly_6_period_return_positive": None if monthly6 is None else monthly6 > 0,
    }
    available_weight = sum(
        tech.WEIGHTS[key] for key, value in checks.items() if value is not None
    )
    earned_weight = sum(
        tech.WEIGHTS[key] for key, value in checks.items() if value is True
    )
    trend_score = round(earned_weight * 100 / available_weight, 1) if available_weight else None
    positive_volumes = [
        row.get("volume") for row in d[-20:]
        if isinstance(row.get("volume"), (int, float)) and row["volume"] > 0
    ]
    average_volume = sum(positive_volumes) / len(positive_volumes) if positive_volumes else None
    latest_volume = d[-1].get("volume")
    volume_ratio = (
        latest_volume / average_volume
        if isinstance(latest_volume, (int, float)) and average_volume else None
    )

    factors = {
        "multi_timeframe_trend": trend_score,
        "daily_market_structure": tech.market_structure_assessment(d).get("score"),
        "weekly_market_structure": tech.market_structure_assessment(w, lookback=52).get("score"),
        "monthly_market_structure": tech.market_structure_assessment(m, lookback=36).get("score"),
        "volume_price_pressure": tech.volume_price_assessment(d).get("score"),
        "breakout_structure": tech.breakout_position_assessment(d, volume_ratio).get("score"),
        "trend_efficiency": tech.trend_efficiency_assessment(d, period=20).get("score"),
        "volatility_quality": tech.volatility_quality_assessment(d).get("score"),
    }
    technical_score, factor_weight = tech.weighted_technical_score(factors)
    if technical_score is None or factor_weight <= 0:
        return None
    penalty, _, _, _ = tech.assess_heat(rsi_value, daily20, daily60, monthly6, volume_ratio)
    risk_adjusted = round(max(0.0, float(technical_score) - float(penalty)), 1)
    return {
        "symbol": str(stock.get("symbol") or ""),
        "technical_score": float(technical_score),
        "risk_adjusted_score": risk_adjusted,
        "technical_factor_available_weight": factor_weight,
        "momentum_pct": {
            "daily_20_period": daily20,
            "daily_60_period": daily60,
            "weekly_13_period": weekly13,
            "monthly_6_period": monthly6,
        },
        "volume_vs_20_period_average": volume_ratio,
        "forward_returns_pct": {
            str(h): forward_return_pct(daily, daily_dates, cutoff, h) for h in HORIZONS
        },
    }


def _midranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        midrank = ((start + 1) + (end + 1)) / 2.0
        for pos in range(start, end + 1):
            ranks[order[pos]] = midrank
        start = end + 1
    return ranks


def spearman_correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        return None
    x, y = _midranks(left), _midranks(right)
    x_bar, y_bar = statistics.fmean(x), statistics.fmean(y)
    numerator = sum((a - x_bar) * (b - y_bar) for a, b in zip(x, y))
    denom_x = sum((a - x_bar) ** 2 for a in x)
    denom_y = sum((b - y_bar) ** 2 for b in y)
    denominator = math.sqrt(denom_x * denom_y)
    if denominator <= 0:
        return None
    value = numerator / denominator
    return round(value, 6) if math.isfinite(value) else None


def _avg(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 4) if values else None


def evaluate_fold(cutoff: date, items: list[dict[str, Any]]) -> dict[str, Any]:
    tech = _technical_module()
    tech.add_relative_momentum_tiebreaker(items)
    items.sort(key=tech.technical_rank_key)
    n = len(items)
    top_n = max(1, math.ceil(n * 0.20))
    bottom_n = max(1, math.ceil(n * 0.20))
    fold = {"cutoff_date": cutoff.isoformat(), "ranked_universe": n, "horizons": {}}
    for horizon in HORIZONS:
        key = str(horizon)
        available = [row for row in items if row["forward_returns_pct"].get(key) is not None]
        returns = [float(row["forward_returns_pct"][key]) for row in available]
        top = [
            float(row["forward_returns_pct"][key])
            for row in items[:top_n] if row["forward_returns_pct"].get(key) is not None
        ]
        bottom = [
            float(row["forward_returns_pct"][key])
            for row in items[-bottom_n:] if row["forward_returns_pct"].get(key) is not None
        ]
        ic = spearman_correlation(
            [float(row["technical_rank_score"]) for row in available],
            [float(row["forward_returns_pct"][key]) for row in available],
        )
        top_mean, bottom_mean, universe_mean = _avg(top), _avg(bottom), _avg(returns)
        fold["horizons"][key] = {
            "observations": len(available),
            "spearman_rank_ic": ic,
            "top_quintile_count": len(top),
            "bottom_quintile_count": len(bottom),
            "top_quintile_mean_return_pct": top_mean,
            "bottom_quintile_mean_return_pct": bottom_mean,
            "universe_mean_return_pct": universe_mean,
            "top_quintile_excess_vs_universe_pct": (
                round(top_mean - universe_mean, 4)
                if top_mean is not None and universe_mean is not None else None
            ),
            "top_minus_bottom_quintile_spread_pct": (
                round(top_mean - bottom_mean, 4)
                if top_mean is not None and bottom_mean is not None else None
            ),
            "top_quintile_win_rate_pct": (
                round(100.0 * sum(v > 0 for v in top) / len(top), 2) if top else None
            ),
        }
    return fold


def summarize_folds(folds: list[dict[str, Any]]) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    for horizon in HORIZONS:
        key = str(horizon)
        rows = [fold["horizons"][key] for fold in folds if key in fold.get("horizons", {})]
        def nums(field: str) -> list[float]:
            return [
                float(row[field]) for row in rows
                if isinstance(row.get(field), (int, float)) and math.isfinite(float(row[field]))
            ]
        summaries[key] = {
            "fold_count": sum(1 for row in rows if row.get("observations", 0) > 0),
            "pooled_forward_observations": sum(int(row.get("observations", 0)) for row in rows),
            "median_spearman_rank_ic": _median(nums("spearman_rank_ic")),
            "mean_spearman_rank_ic": _avg(nums("spearman_rank_ic")),
            "median_top_quintile_excess_vs_universe_pct": _median(nums("top_quintile_excess_vs_universe_pct")),
            "median_top_minus_bottom_quintile_spread_pct": _median(nums("top_minus_bottom_quintile_spread_pct")),
            "median_top_quintile_win_rate_pct": _median(nums("top_quintile_win_rate_pct")),
            "median_top_quintile_mean_return_pct": _median(nums("top_quintile_mean_return_pct")),
            "median_universe_mean_return_pct": _median(nums("universe_mean_return_pct")),
        }
    return summaries


def classify_validation_status(
    enough_history: bool, valid_20: dict[str, Any], valid_60: dict[str, Any]
) -> str:
    """Use absolute profitability and win-rate checks, not relative excess alone."""
    if not enough_history:
        return "insufficient_history_for_validation"

    positive_relative_edge = all((
        isinstance(valid_20.get("median_spearman_rank_ic"), (int, float))
        and valid_20["median_spearman_rank_ic"] > 0,
        isinstance(valid_60.get("median_spearman_rank_ic"), (int, float))
        and valid_60["median_spearman_rank_ic"] > 0,
        isinstance(valid_20.get("median_top_quintile_excess_vs_universe_pct"), (int, float))
        and valid_20["median_top_quintile_excess_vs_universe_pct"] > 0,
        isinstance(valid_60.get("median_top_quintile_excess_vs_universe_pct"), (int, float))
        and valid_60["median_top_quintile_excess_vs_universe_pct"] > 0,
    ))
    positive_absolute_outcome = all(
        isinstance(horizon.get("median_top_quintile_mean_return_pct"), (int, float))
        and horizon["median_top_quintile_mean_return_pct"] > 0
        and isinstance(horizon.get("median_top_quintile_win_rate_pct"), (int, float))
        and horizon["median_top_quintile_win_rate_pct"] >= 50
        for horizon in (valid_20, valid_60)
    )
    if positive_relative_edge and positive_absolute_outcome:
        return "positive_out_of_sample_evidence"
    if positive_relative_edge:
        return "relative_edge_but_mixed_absolute_returns"
    return "mixed_or_negative_out_of_sample_evidence"


def build_validation(stocks: list[dict[str, Any]], tech: Any | None = None) -> dict[str, Any]:
    tech = tech or _technical_module()
    records: dict[str, list[dict[str, Any]]] = {d.isoformat(): [] for d in CUTOFF_DATES}
    file_symbols = 0
    missing_history = 0
    for idx, stock in enumerate(stocks, start=1):
        history = stock.get("history") if isinstance(stock.get("history"), dict) else {}
        def load_frame(frame: str) -> list[dict[str, Any]]:
            entry = history.get(frame) if isinstance(history.get(frame), dict) else {}
            return clean_candles(tech.read_candles(entry.get("file")))
        daily, weekly, monthly = load_frame("1D"), load_frame("W"), load_frame("M")
        if not daily or not weekly or not monthly:
            missing_history += 1
            continue
        file_symbols += 1
        daily_dates = candle_dates(daily)
        for cutoff in CUTOFF_DATES:
            signal = score_as_of(stock, daily, weekly, monthly, cutoff, tech)
            if signal is not None:
                records[cutoff.isoformat()].append(signal)
        if idx % 100 == 0 or idx == len(stocks):
            print(f"WALK_FORWARD_PROGRESS:{idx}/{len(stocks)} history_symbols={file_symbols} missing={missing_history}")

    folds = []
    for cutoff in CUTOFF_DATES:
        rows = records[cutoff.isoformat()]
        if len(rows) >= 50:
            folds.append(evaluate_fold(cutoff, rows))
        else:
            folds.append({
                "cutoff_date": cutoff.isoformat(), "ranked_universe": len(rows),
                "status": "insufficient_cross_sectional_sample", "horizons": {},
            })
    horizon_summary = summarize_folds([f for f in folds if "horizons" in f])
    valid_20 = horizon_summary.get("20", {})
    valid_60 = horizon_summary.get("60", {})
    overall_observations = min(
        int(valid_20.get("pooled_forward_observations", 0)),
        int(valid_60.get("pooled_forward_observations", 0)),
    )
    enough_history = (
        int(valid_20.get("fold_count", 0)) >= 8
        and int(valid_60.get("fold_count", 0)) >= 8
        and overall_observations >= 1500
    )
    validation_status = classify_validation_status(enough_history, valid_20, valid_60)
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "validation_status": validation_status,
        "purpose": "Retrospective walk-forward test of the existing technical rank; not a profit guarantee or a fitted model.",
        "dataset": {
            "current_universe_count": len(stocks),
            "symbols_with_all_three_timeframes": file_symbols,
            "symbols_missing_one_or_more_timeframes": missing_history,
            "cutoff_dates": [d.isoformat() for d in CUTOFF_DATES],
            "folds_with_50_or_more_ranked_symbols": sum(1 for f in folds if f["ranked_universe"] >= 50),
            "min_ranked_symbols_in_fold": min((f["ranked_universe"] for f in folds), default=0),
            "max_ranked_symbols_in_fold": max((f["ranked_universe"] for f in folds), default=0),
            "price_history_cutoff_rule": "daily candles <= cutoff; weekly candles at least 7 days old; monthly candles at least 32 days old",
        },
        "return_definition": {
            "entry": "next trading session open after cutoff",
            "exit": "close of the Nth future trading session (N=20,60,120)",
            "includes_dividends": False,
            "transaction_costs_or_slippage_included": False,
        },
        "methodology": {
            "ranking": "The current eight-factor technical score and heat penalty are recomputed as of each cutoff using only history then available. Relative-momentum percentiles are recalculated cross-sectionally within each fold using the same 70/30 rank blend.",
            "metrics": "Spearman IC between rank and forward return, equal-weight top/bottom quintile spreads, top-quintile win rate, and top-quintile excess versus the eligible universe.",
            "no_hyperparameter_fit": True,
        },
        "horizons": horizon_summary,
        "folds": folds,
        "limitations": [
            "Survivorship bias: only symbols in the current stock universe are available; delisted securities are not reconstructed.",
            "Returns are price-only and exclude dividends, taxes, transaction costs, slippage, and limit-up/down execution constraints.",
            "Historical corporate-action adjustment quality of the source series has not been independently audited.",
            "A positive rank IC or spread is historical evidence, not proof of future profitability.",
        ],
    }


def main() -> None:
    with UNIVERSE_FILE.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    stocks = payload.get("stocks", [])
    if not isinstance(stocks, list) or not stocks:
        raise ValueError("Stock universe is missing or has no rows.")
    report = build_validation(stocks)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("RADAR_VALIDATION_STATUS:", report["validation_status"])
    print("RANKED_FOLDS:", report["dataset"]["folds_with_50_or_more_ranked_symbols"])
    print("RANKED_UNIVERSE_RANGE:", report["dataset"]["min_ranked_symbols_in_fold"], report["dataset"]["max_ranked_symbols_in_fold"])
    for horizon, row in report["horizons"].items():
        print(
            f"FORWARD_{horizon}D: folds={row['fold_count']} observations={row['pooled_forward_observations']} "
            f"median_ic={row['median_spearman_rank_ic']} median_top_quintile_excess={row['median_top_quintile_excess_vs_universe_pct']}"
        )
    print("OUTPUT:", OUTPUT_FILE.relative_to(ROOT))


if __name__ == "__main__":
    main()
