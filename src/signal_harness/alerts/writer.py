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
        "# SignalHarness 重点提醒",
        "",
        "当前仅写入本地文件，不会自动向外部渠道发送。",
        "",
    ]
    if not alerts:
        lines.extend(["_目前没有新的重点提醒。_", ""])
        return "\n".join(lines)
    for alert in alerts:
        title = str(alert.get("title") or alert.get("event_id") or "未命名变化")
        what = str(alert.get("what_changed_zh") or "").strip() or f"检测到「{title}」这条变化。"
        why = str(alert.get("why_relevant_zh") or "").strip()
        if not why:
            modules = "、".join(str(item) for item in alert.get("affected_modules", [])[:3])
            why = f"这条变化与 {modules or '项目当前使用方式'} 有关联，需要结合实际版本确认影响。"
        actions = [
            str(item).strip()
            for item in alert.get("recommended_actions_zh", [])
            if str(item).strip()
        ]
        lines.extend(
            [
                f"## {title}",
                "",
                f"- 来源：{alert.get('source_name') or '未知'} ({alert.get('source_type') or 'unknown'})",
                f"- 当前判断：{alert.get('decision') or 'unknown'}",
                f"- 分类：{alert.get('category') or 'unknown'}",
                f"- 影响分：{float(alert.get('impact_score', 0)):.2f}",
                "",
                "**发生了什么**",
                "",
                what,
                "",
                "**为什么与你有关**",
                "",
                why,
                "",
                "**建议怎么做**",
                "",
            ]
        )
        lines.extend([f"- {item}" for item in actions] or ["- 暂无需要立即执行的动作。"] )
        if alert.get("url"):
            lines.extend(["", f"原始来源：{alert['url']}"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"

