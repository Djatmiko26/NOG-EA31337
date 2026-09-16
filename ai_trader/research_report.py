from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DECISIONS_FILE = DATA_DIR / "decisions.csv"
OUTCOMES_FILE = DATA_DIR / "outcomes.csv"
REPORT_FILE = DATA_DIR / "research_report.md"

HORIZONS = (5, 10, 20)


def clean_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or pd.isna(value):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def confidence_bucket(value: Any) -> str:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return "UNKNOWN"

    score = int(float(value))
    if score < 60:
        return "00-59"
    if score < 70:
        return "60-69"
    if score < 80:
        return "70-79"
    if score < 90:
        return "80-89"
    return "90-100"


def numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(df[column], errors="coerce").dropna()


def summarize_returns(
    df: pd.DataFrame,
    return_column: str,
    *,
    mfe_column: str | None = None,
    mae_column: str | None = None,
) -> dict[str, float | int | None]:
    values = numeric_series(df, return_column)

    if values.empty:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "win_rate": None,
            "loss_rate": None,
            "flat_rate": None,
            "p25": None,
            "p75": None,
            "avg_mfe": None,
            "avg_mae": None,
        }

    total = len(values)
    result: dict[str, float | int | None] = {
        "n": total,
        "mean": float(values.mean()),
        "median": float(values.median()),
        "win_rate": float((values > 0).sum() / total * 100.0),
        "loss_rate": float((values < 0).sum() / total * 100.0),
        "flat_rate": float((values == 0).sum() / total * 100.0),
        "p25": float(values.quantile(0.25)),
        "p75": float(values.quantile(0.75)),
        "avg_mfe": None,
        "avg_mae": None,
    }

    if mfe_column:
        mfe = numeric_series(df, mfe_column)
        if not mfe.empty:
            result["avg_mfe"] = float(mfe.mean())

    if mae_column:
        mae = numeric_series(df, mae_column)
        if not mae.empty:
            result["avg_mae"] = float(mae.mean())

    return result


def fmt_number(value: float | int | None, digits: int = 3) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}"


def fmt_pct(value: float | int | None, digits: int = 3) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}%"


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        text = str(value)
        return text.replace("|", "\\|").replace("\n", " ")

    header = "| " + " | ".join(cell(v) for v in headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join([header, separator, *body])


def filter_symbol_timeframe(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    if "symbol" not in df.columns or "timeframe" not in df.columns:
        return df.copy()

    return df[
        (df["symbol"].astype(str) == symbol)
        & (df["timeframe"].astype(str) == timeframe)
    ].copy()


def directional_mask(series: pd.Series) -> pd.Series:
    return series.astype(str).str.upper().isin({"BUY", "SELL"})


def build_performance_rows(
    df: pd.DataFrame,
    prefix: str,
) -> list[list[Any]]:
    rows: list[list[Any]] = []

    for horizon in HORIZONS:
        stats = summarize_returns(
            df,
            f"{prefix}_return_{horizon}_pct",
            mfe_column=f"{prefix}_mfe_20_pct" if horizon == 20 else None,
            mae_column=f"{prefix}_mae_20_pct" if horizon == 20 else None,
        )

        rows.append(
            [
                horizon,
                stats["n"],
                fmt_pct(stats["mean"]),
                fmt_pct(stats["median"]),
                fmt_pct(stats["win_rate"], 1),
                fmt_pct(stats["loss_rate"], 1),
                fmt_pct(stats["avg_mfe"]) if horizon == 20 else "-",
                fmt_pct(stats["avg_mae"]) if horizon == 20 else "-",
            ]
        )

    return rows


def group_performance_rows(
    df: pd.DataFrame,
    group_column: str,
    return_column: str,
    mfe_column: str,
    mae_column: str,
) -> list[list[Any]]:
    rows: list[list[Any]] = []

    if df.empty or group_column not in df.columns:
        return rows

    for group, group_df in df.groupby(group_column, dropna=False):
        stats = summarize_returns(
            group_df,
            return_column,
            mfe_column=mfe_column,
            mae_column=mae_column,
        )
        rows.append(
            [
                group,
                stats["n"],
                fmt_pct(stats["mean"]),
                fmt_pct(stats["median"]),
                fmt_pct(stats["win_rate"], 1),
                fmt_pct(stats["avg_mfe"]),
                fmt_pct(stats["avg_mae"]),
            ]
        )

    return rows


def build_research_report(
    decisions: pd.DataFrame,
    outcomes: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
) -> str:
    decisions = filter_symbol_timeframe(decisions, symbol, timeframe)
    outcomes = filter_symbol_timeframe(outcomes, symbol, timeframe)

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        "# NOG OpenAI Trader - Research Report",
        "",
        f"Generated: **{generated}**  ",
        f"Market: **{symbol} {timeframe}**",
        "",
        "> Research only. This report does not authorize order execution and does not prove future profitability.",
        "",
    ]

    total_decisions = len(decisions)

    if total_decisions:
        qualified = decisions.get("filter_qualifies", pd.Series(False, index=decisions.index)).map(clean_bool)
        called = decisions.get("openai_called", pd.Series(False, index=decisions.index)).map(clean_bool)
        qualified_count = int(qualified.sum())
        called_count = int(called.sum())
        skipped_count = total_decisions - called_count
        call_rate = called_count / total_decisions * 100.0
        skip_rate = skipped_count / total_decisions * 100.0
    else:
        qualified_count = called_count = skipped_count = 0
        call_rate = skip_rate = 0.0

    lines.extend(
        [
            "## Pipeline / API Call Efficiency",
            "",
            md_table(
                ["Metric", "Value"],
                [
                    ["Processed closed candles", total_decisions],
                    ["Local filter qualified", qualified_count],
                    ["OpenAI calls", called_count],
                    ["OpenAI calls skipped", skipped_count],
                    ["API call rate", fmt_pct(call_rate, 1)],
                    ["API skip rate", fmt_pct(skip_rate, 1)],
                ],
            ),
            "",
        ]
    )

    if total_decisions and "action" in decisions.columns:
        called_df = decisions[
            decisions.get("openai_called", pd.Series(False, index=decisions.index)).map(clean_bool)
        ].copy()
        if not called_df.empty:
            counts = called_df["action"].fillna("").astype(str).str.upper().value_counts()
            lines.extend(
                [
                    "### OpenAI classification mix",
                    "",
                    md_table(
                        ["Action", "Count", "Share of calls"],
                        [
                            [
                                action if action else "BLANK",
                                int(count),
                                fmt_pct(count / len(called_df) * 100.0, 1),
                            ]
                            for action, count in counts.items()
                        ],
                    ),
                    "",
                ]
            )

    if outcomes.empty:
        lines.extend(
            [
                "## Mature Outcome Data",
                "",
                "No 20-bar outcome labels are available yet. This is normal for a newly started monitor.",
                "Run `python outcome_labeller.py` after decisions have at least 20 future closed bars, then run this report again.",
                "",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "## Mature Outcome Data",
            "",
            f"Labelled decisions: **{len(outcomes)}**",
            "",
        ]
    )

    if len(outcomes) < 30:
        lines.extend(
            [
                "**Sample-size warning:** fewer than 30 matured decisions are available. Treat all performance numbers as highly preliminary.",
                "",
            ]
        )
    elif len(outcomes) < 100:
        lines.extend(
            [
                "**Sample-size warning:** fewer than 100 matured decisions are available. Results are still preliminary.",
                "",
            ]
        )

    # Local filter directional performance.
    local = outcomes.copy()
    if "filter_qualifies" in local.columns:
        local = local[local["filter_qualifies"].map(clean_bool)]
    if "filter_direction" in local.columns:
        local = local[directional_mask(local["filter_direction"])]
    else:
        local = local.iloc[0:0]

    lines.extend(
        [
            "## Local Filter Directional Performance",
            "",
            "Returns are directional: positive means the candidate direction was correct over that horizon.",
            "",
            md_table(
                ["Bars", "N", "Avg return", "Median", "Win rate", "Loss rate", "Avg MFE", "Avg MAE"],
                build_performance_rows(local, "filter"),
            ),
            "",
        ]
    )

    # OpenAI BUY/SELL performance. WAIT is intentionally excluded from directional-return stats.
    ai = outcomes.copy()
    if "openai_called" in ai.columns:
        ai = ai[ai["openai_called"].map(clean_bool)]
    if "action" in ai.columns:
        ai = ai[directional_mask(ai["action"])]
    else:
        ai = ai.iloc[0:0]

    lines.extend(
        [
            "## OpenAI Directional Performance",
            "",
            "Only OpenAI `BUY` and `SELL` classifications are included. `WAIT` is excluded from directional-return statistics.",
            "",
            md_table(
                ["Bars", "N", "Avg return", "Median", "Win rate", "Loss rate", "Avg MFE", "Avg MAE"],
                build_performance_rows(ai, "openai"),
            ),
            "",
        ]
    )

    # Agreement between deterministic candidate and OpenAI direction.
    if not ai.empty and "filter_direction" in ai.columns and "action" in ai.columns:
        ai = ai.copy()
        ai["agreement"] = (
            ai["filter_direction"].astype(str).str.upper()
            == ai["action"].astype(str).str.upper()
        )
        agree_n = int(ai["agreement"].sum())
        disagree_n = int((~ai["agreement"]).sum())
        agreement_rate = agree_n / len(ai) * 100.0 if len(ai) else 0.0

        agreement_rows: list[list[Any]] = []
        for label, mask in [
            ("AGREE", ai["agreement"]),
            ("DISAGREE", ~ai["agreement"]),
        ]:
            subset = ai[mask]
            stats = summarize_returns(
                subset,
                "openai_return_20_pct",
                mfe_column="openai_mfe_20_pct",
                mae_column="openai_mae_20_pct",
            )
            agreement_rows.append(
                [
                    label,
                    stats["n"],
                    fmt_pct(stats["mean"]),
                    fmt_pct(stats["win_rate"], 1),
                    fmt_pct(stats["avg_mfe"]),
                    fmt_pct(stats["avg_mae"]),
                ]
            )

        lines.extend(
            [
                "## Local Filter vs OpenAI Agreement",
                "",
                f"Directional agreement rate: **{agreement_rate:.1f}%** ({agree_n} agree / {disagree_n} disagree).",
                "",
                md_table(
                    ["Group", "N", "Avg 20-bar return", "Win rate", "Avg MFE", "Avg MAE"],
                    agreement_rows,
                ),
                "",
            ]
        )

    # Confidence bucket analysis.
    if not ai.empty and "confidence" in ai.columns:
        confidence_df = ai.copy()
        confidence_df["confidence_bucket"] = confidence_df["confidence"].map(confidence_bucket)
        bucket_rows = group_performance_rows(
            confidence_df,
            "confidence_bucket",
            "openai_return_20_pct",
            "openai_mfe_20_pct",
            "openai_mae_20_pct",
        )
        bucket_order = {"00-59": 0, "60-69": 1, "70-79": 2, "80-89": 3, "90-100": 4, "UNKNOWN": 5}
        bucket_rows.sort(key=lambda row: bucket_order.get(str(row[0]), 99))

        lines.extend(
            [
                "## OpenAI Confidence Buckets",
                "",
                "Confidence is classification confidence, not probability of profit. This table tests whether higher confidence actually correlates with better realised outcomes.",
                "",
                md_table(
                    ["Confidence", "N", "Avg 20-bar return", "Median", "Win rate", "Avg MFE", "Avg MAE"],
                    bucket_rows,
                ),
                "",
            ]
        )

    # Market regime analysis.
    if not ai.empty and "market_regime" in ai.columns:
        regime_df = ai.copy()
        regime_df["market_regime"] = regime_df["market_regime"].fillna("UNKNOWN").astype(str).str.upper()
        regime_rows = group_performance_rows(
            regime_df,
            "market_regime",
            "openai_return_20_pct",
            "openai_mfe_20_pct",
            "openai_mae_20_pct",
        )
        regime_rows.sort(key=lambda row: str(row[0]))

        lines.extend(
            [
                "## OpenAI Performance by Market Regime",
                "",
                md_table(
                    ["Regime", "N", "Avg 20-bar return", "Median", "Win rate", "Avg MFE", "Avg MAE"],
                    regime_rows,
                ),
                "",
            ]
        )

    # Direction breakdown.
    if not ai.empty and "action" in ai.columns:
        direction_df = ai.copy()
        direction_df["action"] = direction_df["action"].astype(str).str.upper()
        direction_rows = group_performance_rows(
            direction_df,
            "action",
            "openai_return_20_pct",
            "openai_mfe_20_pct",
            "openai_mae_20_pct",
        )
        direction_rows.sort(key=lambda row: str(row[0]))

        lines.extend(
            [
                "## OpenAI Performance by Direction",
                "",
                md_table(
                    ["Action", "N", "Avg 20-bar return", "Median", "Win rate", "Avg MFE", "Avg MAE"],
                    direction_rows,
                ),
                "",
            ]
        )

    lines.extend(
        [
            "## Interpretation Rules",
            "",
            "- Do not promote the system to demo execution because of a few winning observations.",
            "- Compare mean and median; a positive mean driven by one outlier is weak evidence.",
            "- MFE/MAE are research measurements, not automatic SL/TP settings.",
            "- Confidence buckets need enough samples before deciding whether a confidence threshold is useful.",
            "- Regime results need enough samples per regime before changing strategy rules.",
            "- The next decision should be data-driven: keep, change, or remove filters only after enough labelled observations exist.",
            "",
        ]
    )

    return "\n".join(lines)


def load_csv_or_empty(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path)


def infer_market(decisions: pd.DataFrame, outcomes: pd.DataFrame) -> tuple[str, str]:
    source = decisions if not decisions.empty else outcomes
    if source.empty:
        return "XAUUSD", "M5"

    symbol = str(source.iloc[-1].get("symbol", "XAUUSD"))
    timeframe = str(source.iloc[-1].get("timeframe", "M5"))
    return symbol, timeframe


def main() -> None:
    decisions = load_csv_or_empty(DECISIONS_FILE)
    outcomes = load_csv_or_empty(OUTCOMES_FILE)

    if decisions.empty and outcomes.empty:
        print(
            "Belum ada data research. Jalankan market_monitor.py terlebih dahulu.",
            flush=True,
        )
        return

    symbol, timeframe = infer_market(decisions, outcomes)
    report = build_research_report(
        decisions,
        outcomes,
        symbol=symbol,
        timeframe=timeframe,
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(report, encoding="utf-8")

    print(report, flush=True)
    print(f"\nSaved report: {REPORT_FILE}", flush=True)


if __name__ == "__main__":
    main()
