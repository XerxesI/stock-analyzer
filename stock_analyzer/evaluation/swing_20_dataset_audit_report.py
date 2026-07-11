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
            "## 12. Recommended Next Step",
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

