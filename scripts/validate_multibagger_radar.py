#!/usr/bin/env python3
"""Walk-forward test: can the technical radar identify future 2x/3x shares?

This deliberately tests multi-bagger labels instead of treating a high technical
score as an 80% probability. It uses the existing as-of-date scoring engine,
future daily CLOSE prices, multiple historical cutoffs and current-universe
members only. Survivorship, corporate-action adjustment, liquidity, dividends,
costs and overlapping horizons remain limitations and are disclosed in output.
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_FILE = ROOT / "data" / "stock_universe.json"
OUTPUT_FILE = ROOT / "data" / "technical_multibagger_validation.json"
HORIZONS = (240, 500)  # trading sessions, approximately 1 and 2 years
MULTIPLES = (2.0, 3.0)  # 2x = +100%; 3x = +200%
CUTOFF_DATES = tuple(
    date(year, month, day)
    for year in range(2018, 2024)
    for month, day in ((6, 30), (12, 31))
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_series(stock: dict[str, Any], tech: Any) -> dict[str, list[dict[str, Any]]]:
    history = stock.get("history") or {}
    return {
        timeframe: tech.read_candles((history.get(key) or {}).get("file"))
        for timeframe, key in (("daily", "1D"), ("weekly", "W"), ("monthly", "M"))
    }


def date_for(candle: dict[str, Any]) -> date:
    return datetime.fromtimestamp(int(candle["time"]), timezone.utc).date()


def maximum_forward_multiple(
    daily: list[dict[str, Any]], cutoff: date, horizon: int
) -> float | None:
    """Maximum future daily CLOSE / next session OPEN over exactly N future sessions."""
    valid = []
    for row in daily:
        try:
            ts = int(row["time"])
            close = float(row["close"])
            op = float(row["open"])
            if ts > 0 and math.isfinite(close) and math.isfinite(op) and close > 0 and op > 0:
                valid.append({"date": date_for(row), "open": op, "close": close})
        except (KeyError, ValueError, TypeError, OverflowError, OSError):
            continue
    valid.sort(key=lambda x: x["date"])
    prior = [i for i, row in enumerate(valid) if row["date"] <= cutoff]
    if not prior:
        return None
    idx = prior[-1]
    start = idx + 1
    stop = start + horizon
    if stop > len(valid):
        return None
    entry = valid[start]["open"]
    if entry <= 0:
        return None
    peak_close = max(row["close"] for row in valid[start:stop])
    return peak_close / entry


def percentile_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "folds": 0, "selected_count": 0, "multibagger_count": 0,
            "precision_pct": None, "median_fold_precision_pct": None,
            "base_rate_pct": None, "precision_lift": None, "recall_pct": None,
        }
    selected = sum(x["selected_count"] for x in rows)
    positives = sum(x["positive_count"] for x in rows)
    all_count = sum(x["eligible_count"] for x in rows)
    all_positives = sum(x["eligible_positive_count"] for x in rows)
    fold_precisions = [
        100 * x["positive_count"] / x["selected_count"]
        for x in rows if x["selected_count"] > 0
    ]
    precision = 100 * positives / selected if selected else None
    base = 100 * all_positives / all_count if all_count else None
    recall = 100 * positives / all_positives if all_positives else None
    return {
        "folds": len(rows),
        "selected_count": selected,
        "multibagger_count": positives,
        "precision_pct": round(precision, 2) if precision is not None else None,
        "median_fold_precision_pct": round(statistics.median(fold_precisions), 2) if fold_precisions else None,
        "base_rate_pct": round(base, 2) if base is not None else None,
        "precision_lift": round(precision / base, 3) if precision is not None and base else None,
        "recall_pct": round(recall, 2) if recall is not None else None,
    }


def main() -> None:
    try:
        from scripts import build_technical_radar as tech
        from scripts.validate_radar_scores import score_as_of
    except ModuleNotFoundError:
        import build_technical_radar as tech
        from validate_radar_scores import score_as_of

    universe = read_json(UNIVERSE_FILE)
    stocks = universe.get("stocks", [])
    cached = []
    errors = 0
    for index, stock in enumerate(stocks, start=1):
        try:
            series = load_series(stock, tech)
            if not series["daily"] or not series["weekly"] or not series["monthly"]:
                continue
            cached.append((stock, series))
        except Exception as exc:
            errors += 1
            print(f"LOAD_ERROR {stock.get('symbol', '?')}: {exc}")
        if index % 100 == 0:
            print(f"Loaded histories {index}/{len(stocks)}")

    fold_results: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = {
        f"{h}x{int(multiple)}": {str(pct): [] for pct in (5, 10, 20)}
        for h in HORIZONS for multiple in MULTIPLES
    }
    fold_summaries = []
    for cutoff in CUTOFF_DATES:
        scored = []
        for stock, series in cached:
            item = score_as_of(
                stock, series["daily"], series["weekly"], series["monthly"], cutoff, tech
            )
            if item is None:
                continue
            item["future_multiples"] = {
                str(h): maximum_forward_multiple(series["daily"], cutoff, h)
                for h in HORIZONS
            }
            scored.append(item)
        if len(scored) < 100:
            fold_summaries.append({
                "cutoff_date": cutoff.isoformat(),
                "eligible_count": len(scored),
                "status": "insufficient_universe",
            })
            continue

        tech.add_relative_momentum_tiebreaker(scored)
        scored.sort(key=tech.technical_rank_key)
        per_fold_targets = {}
        for horizon in HORIZONS:
            eligible = [
                item for item in scored
                if item["future_multiples"].get(str(horizon)) is not None
            ]
            for multiple in MULTIPLES:
                key = f"{horizon}x{int(multiple)}"
                labelled = [
                    item for item in eligible
                    if item["future_multiples"][str(horizon)] >= multiple
                ]
                for pct in (5, 10, 20):
                    n = max(1, math.ceil(len(eligible) * pct / 100))
                    selected = eligible[:n]
                    positives = sum(
                        1 for item in selected
                        if item["future_multiples"][str(horizon)] >= multiple
                    )
                    fold_results[key][str(pct)].append({
                        "cutoff_date": cutoff.isoformat(),
                        "eligible_count": len(eligible),
                        "eligible_positive_count": len(labelled),
                        "selected_count": len(selected),
                        "positive_count": positives,
                    })
                    per_fold_targets[f"{key}_top{pct}pct"] = {
                        "selected_count": len(selected),
                        "positive_count": positives,
                        "precision_pct": round(100 * positives / len(selected), 2),
                        "base_rate_pct": round(100 * len(labelled) / len(eligible), 2),
                    }
        fold_summaries.append({
            "cutoff_date": cutoff.isoformat(),
            "eligible_count": len(scored),
            "targets": per_fold_targets,
        })
        print(f"FOLD {cutoff.isoformat()} eligible={len(scored)}")

    results = {}
    for target, by_percentile in fold_results.items():
        results[target] = {
            f"top_{pct}_percent": percentile_summary(rows)
            for pct, rows in by_percentile.items()
        }

    checks = []
    for target, by_percentile in results.items():
        top5 = by_percentile["top_5_percent"]
        checks.append(
            top5["precision_pct"] is not None
            and top5["precision_pct"] >= 80
            and top5["median_fold_precision_pct"] is not None
            and top5["median_fold_precision_pct"] >= 70
            and top5["selected_count"] >= 100
            and top5["folds"] >= 8
        )
    passed = bool(checks) and all(checks)
    report = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "80pct_precision_gate_passed" if passed else "80pct_precision_not_validated",
        "purpose": "Walk-forward precision/recall test of technical ranking for future 2x and 3x price peaks; not a probability forecast.",
        "dataset": {
            "current_universe_count": len(stocks),
            "histories_loaded": len(cached),
            "history_load_errors": errors,
            "cutoff_dates": [x.isoformat() for x in CUTOFF_DATES],
            "folds_with_usable_universe": sum(1 for x in fold_summaries if x.get("targets")),
            "ranking_model": "existing multi-timeframe technical score + market structure + volume/price pressure + breakout + trend efficiency + volatility quality + relative momentum; recomputed as of each cutoff",
        },
        "label_definition": {
            "2x": "At least one future daily close >= 2x the next-session open within the horizon.",
            "3x": "At least one future daily close >= 3x the next-session open within the horizon.",
            "horizons_trading_sessions": {"240": "approximately 1 year", "500": "approximately 2 years"},
            "not_a_terminal_return": True,
        },
        "methodology": {
            "selection": "Top 5%, 10%, and 20% of eligible symbols by technical rank in each fold.",
            "metrics": "Pooled precision, median fold precision, base rate, precision lift, recall and selected counts.",
            "current_universe_only": True,
            "survivorship_bias": "Material: the universe contains currently available symbols only; delisted/merged failures may be missing.",
            "corporate_action_adjustment": "Price-history adjustment status is not independently verified; split/bonus/dividend distortions may affect labels.",
            "overlapping_horizons": "Fold windows overlap and are not independent observations.",
            "costs_liquidity_dividends": "Not included.",
            "probability_calibration": "No probability is displayed unless the predeclared precision gate passes; passing precision is still not a calibrated individual-stock probability.",
        },
        "results": results,
        "folds": fold_summaries,
    }
    OUTPUT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OUTPUT {OUTPUT_FILE}")
    print(f"STATUS {report['status']}")
    for target, by_percentile in results.items():
        top5 = by_percentile["top_5_percent"]
        print(
            f"{target} top5 precision={top5['precision_pct']}% "
            f"median_fold={top5['median_fold_precision_pct']}% "
            f"base={top5['base_rate_pct']}% selected={top5['selected_count']}"
        )


if __name__ == "__main__":
    main()
