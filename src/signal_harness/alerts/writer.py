"""Write local alert artifacts; no external notification dispatch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from signal_harness.alerts.policy import AlertPolicy, select_alerts
from signal_harness.alerts.state import load_alerted_event_ids, save_alert_state
from signal_harness.signal.schemas import SignalAssessment, SignalEvent
from signal_harness.utils.fs import atomic_write_text


def write_alert_outputs(
    *,
    output_dir: str | Path,
    state_dir: str | Path,
    events: list[SignalEvent],
    assessments: list[SignalAssessment],
    policy: AlertPolicy,
) -> dict[str, Path]:
    """Write alerts.json, alerts.md, and alert_state.json."""

    output_root = Path(output_dir).expanduser().resolve()
    state_root = Path(state_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    state_root.mkdir(parents=True, exist_ok=True)
    state_path = state_root / "alert_state.json"
    already_alerted = load_alerted_event_ids(state_path)
    alerts = select_alerts(
        events,
        assessments,
        policy=policy,
        already_alerted=already_alerted,
    )
    all_alerted = already_alerted | {
        str(alert["event_id"]) for alert in alerts if alert.get("event_id")
    }
    json_path = output_root / "alerts.json"
    md_path = output_root / "alerts.md"
    atomic_write_text(
        json_path,
        json.dumps(alerts, indent=2, ensure_ascii=False) + "\n",
    )
    atomic_write_text(md_path, _render_alerts_markdown(alerts))
    save_alert_state(
        state_path,
        alerted_event_ids=all_alerted,
        latest_alerts=alerts,
    )
    return {
        "alerts_json": json_path,
        "alerts_markdown": md_path,
        "alert_state": state_path,
    }


def _render_alerts_markdown(alerts: list[dict[str, Any]]) -> str:
    lines = [
        "# SignalHarness Alerts",
        "",
        "Default dispatcher: local files only. No external notification was sent.",
        "",
    ]
    if not alerts:
        lines.extend(["_No new alerts._", ""])
        return "\n".join(lines)
    for alert in alerts:
        lines.extend(
            [
                f"## {alert.get('title') or alert.get('event_id')}",
                "",
                f"- Event ID: `{alert.get('event_id')}`",
                f"- Source: {alert.get('source_name')} ({alert.get('source_type')})",
                f"- Decision: {alert.get('decision')}",
                f"- Category: {alert.get('category')}",
                f"- Impact score: {float(alert.get('impact_score', 0)):.2f}",
                f"- Confidence: {float(alert.get('confidence', 0)):.2f}",
                "- Affected modules: "
                + ", ".join(str(item) for item in alert.get("affected_modules", [])),
                "- Reasons:",
            ]
        )
        lines.extend(f"  - {reason}" for reason in alert.get("reasons", []))
        if alert.get("url"):
            lines.append(f"- URL: {alert['url']}")
        lines.extend(["", "_External dispatch disabled._", ""])
    return "\n".join(lines).rstrip() + "\n"

