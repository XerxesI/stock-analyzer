"""Render a SWING_20 audit result as Markdown."""

from __future__ import annotations

from stock_analyzer.datasets.swing_20.schema import AuditResult


def render_markdown(result: AuditResult) -> str:
    """Render the audit result in the report structure defined by MVP 1."""

    data = result.to_dict()
    decision = data["decision"]
    return "\n".join(
        [
            "# SWING_20 Dataset Audit",
            "",
            "## 1. Executive Summary",
            "",
            f"- Strategy: `{data['strategy']}`",
            f"- Spec version: `{data['spec_version']}`",
            f"- Generated at: `{data['generated_at']}`",
            f"- Date range: `{data['date_range'].get('start')}` to `{data['date_range'].get('end')}`",
            f"- Trainability decision: **{decision['status']}**",
            "",
            "## 2. Trainability Decision",
            "",
            f"**Status:** `{decision['status']}`",
            "",
            "### Hard Blockers",
            _bullet_list(decision.get("hard_blockers", [])),
            "",
            "### Warnings",
            _bullet_list(decision.get("warnings", [])),
            "",
            "### Reasons",
            _bullet_list(decision.get("reasons", [])),
            "",
            f"**Recommended next step:** {decision.get('recommended_next_step', '')}",
            "",
            "## 3. Universe Audit",
            "",
            _key_value_table(data["universe"]),
            "",
            "## 4. Label Distribution",
            "",
            _key_value_table(data["labels"]),
            "",
            "## 5. Temporal Stability",
            "",
            _split_table(data["splits"]),
            "",
            "## 6. Regime and Segment Distribution",
            "",
            "Regime and segment distribution are not fully implemented in the initial audit foundation.",
            "",
            "## 7. Event Deduplication",
            "",
            _event_section(data["labels"]),
            "",
            "## 8. Outcome Profile",
            "",
            _outcome_section(data["labels"]),
            "",
            "## 9. Data Quality",
            "",
            _key_value_table(data["quality"].get("counts", {})),
            "",
            "## 10. Leakage and Point-in-Time Risks",
            "",
            _bullet_list(data["quality"].get("point_in_time_limitations", [])),
            "",
            "## 11. Baseline Rates",
            "",
            _key_value_table(data["baseline"]),
            "",
            "## 12. Data Quality Quarantine",
            "",
            _quarantine_section(data.get("data_quality_quarantine", {})),
            "",
            "## 13. Target-Already-Reached-at-Entry Exclusion",
            "",
            _key_value_table(_gap_summary_keys(data.get("target_already_reached_at_entry", {}))),
            "",
            "## 14. Recommended Next Step",
            "",
            decision.get("recommended_next_step", ""),
            "",
        ]
    )


def _bullet_list(items: list[object]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- `{item}`" for item in items)


def _key_value_table(values: dict[str, object]) -> str:
    if not values:
        return "_No data._"
    rows = ["| Metric | Value |", "|---|---:|"]
    for key, value in values.items():
        rows.append(f"| `{key}` | `{value}` |")
    return "\n".join(rows)


def _split_table(splits: dict[str, dict[str, object]]) -> str:
    if not splits:
        return "_No split data._"
    rows = ["| Split | Start | End | Observations | Positives | Positive Rate |", "|---|---:|---:|---:|---:|---:|"]
    for split, values in splits.items():
        rows.append(
            "| "
            + " | ".join(
                [
                    f"`{split}`",
                    f"`{values.get('start')}`",
                    f"`{values.get('end')}`",
                    f"`{values.get('observations')}`",
                    f"`{values.get('raw_positive_observations')}`",
                    f"`{values.get('positive_rate')}`",
                ]
            )
            + " |"
        )
    return "\n".join(rows)


def _event_section(labels: dict[str, object]) -> str:
    keys = [
        "raw_positive_observations",
        "deduplicated_positive_events",
        "raw_to_event_inflation_factor",
        "median_positive_run_length",
        "p90_positive_run_length",
        "max_positive_run_length",
        "top_10_ticker_event_share",
    ]
    return _key_value_table({key: labels.get(key) for key in keys})


def _outcome_section(labels: dict[str, object]) -> str:
    keys = ["mfe_20d_median", "mae_20d_median", "close_return_20d_median"]
    return _key_value_table({key: labels.get(key) for key in keys})


def _quarantine_section(quarantine: dict[str, object]) -> str:
    summary_keys = [
        "data_quality_excluded_symbol_count",
        "data_quality_exclusion_reason_counts",
        "observations_removed_by_data_quality",
        "positive_labels_removed_by_data_quality",
        "events_removed_by_data_quality",
    ]
    summary_table = _key_value_table({key: quarantine.get(key) for key in summary_keys})

    excluded = quarantine.get("data_quality_excluded_symbols") or []
    if not excluded:
        return summary_table + "\n\n_No symbols were quarantined._"

    rows = [
        "| Symbol | Reason | Affected Rows | Non-Positive Price | Material OHLC | First Date | Last Date |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for entry in excluded:
        rows.append(
            "| "
            + " | ".join(
                [
                    f"`{entry.get('symbol')}`",
                    f"`{entry.get('exclusion_reason')}`",
                    f"`{entry.get('affected_row_count')}`",
                    f"`{entry.get('non_positive_price_rows')}`",
                    f"`{entry.get('material_ohlc_inconsistency_rows')}`",
                    f"`{entry.get('first_affected_date')}`",
                    f"`{entry.get('last_affected_date')}`",
                ]
            )
            + " |"
        )
    return summary_table + "\n\n" + "\n".join(rows)


def _gap_summary_keys(gap: dict[str, object]) -> dict[str, object]:
    keys = [
        "excluded_row_count",
        "observations_before",
        "observations_after",
        "raw_positive_before",
        "raw_positive_after",
        "positive_rate_before",
        "positive_rate_after",
        "deduplicated_events_before",
        "deduplicated_events_after",
    ]
    return {key: gap.get(key) for key in keys}

