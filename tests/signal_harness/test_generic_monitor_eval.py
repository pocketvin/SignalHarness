import json
from pathlib import Path

import pytest

from signal_harness.agent_integration.mode import RunMode
from signal_harness.generic_monitor_eval import (
    build_compact_project_brief,
    run_generic_monitor_eval,
)
from signal_harness.providers.adapter import AgentCall


class StubGenericMonitorProvider:
    name = "stub-generic"
    model = "stub-model"

    async def complete(self, call: AgentCall) -> str:
        event_ids = [item["event_id"] for item in call.input_payload["events"]]
        payload = {
            "results": [
                {
                    "event_id": event_ids[0],
                    "category": "dependency_update",
                    "decision": "alert",
                    "reason": "Direct dependency breakage matters.",
                },
                {
                    "event_id": event_ids[1],
                    "category": "noise",
                    "decision": "ignore",
                    "reason": "Unrelated promotion.",
                },
            ]
        }
        return json.dumps(payload)

    async def close(self) -> None:
        return None


def test_compact_project_brief_excludes_deep_structured_state() -> None:
    brief = build_compact_project_brief(
        {
            "project_name": "Demo",
            "goal": "Watch important engineering changes.",
            "tech_stack": ["Python", "FastAPI"],
            "focus_keywords": ["MCP"],
            "dependency_evidence": [
                {"name": "fastapi", "resolved_version": "9.9.9", "confidence": "verified"}
            ],
            "critical_modules": ["private-module-name"],
        }
    )
    assert "Python" in brief
    assert "MCP" in brief
    assert "9.9.9" not in brief
    assert "private-module-name" not in brief


@pytest.mark.asyncio
async def test_generic_monitor_eval_uses_one_live_style_call_without_harness_state(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "events.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "event_id": "gm-1",
                    "source_type": "github_release",
                    "source_name": "fastapi",
                    "title": "Breaking release",
                    "content": "Breaking API change.",
                },
                {
                    "event_id": "gm-2",
                    "source_type": "web_change",
                    "source_name": "promo",
                    "title": "Gaming giveaway",
                    "content": "Consumer promotion.",
                },
            ]
        )
    )
    expectations = tmp_path / "expectations.json"
    expectations.write_text(
        json.dumps(
            {
                "suite": "generic-monitor-test",
                "thresholds": {
                    "decision_accuracy": 1.0,
                    "category_accuracy": 1.0,
                    "priority_precision": 1.0,
                    "priority_recall": 1.0,
                },
                "cases": [
                    {
                        "event_id": "gm-1",
                        "expected_decision": "alert",
                        "expected_category": "dependency_update",
                        "expected_priority": True,
                        "rationale": "direct dependency risk",
                    },
                    {
                        "event_id": "gm-2",
                        "expected_decision": "ignore",
                        "expected_category": "noise",
                        "expected_priority": False,
                        "rationale": "irrelevant noise",
                    },
                ],
            }
        )
    )
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "project_name: Demo\n"
        "goal: Monitor Python API changes.\n"
        "tech_stack: [Python, FastAPI]\n"
        "focus_keywords: [breaking, security]\n"
    )

    summary = await run_generic_monitor_eval(
        provider=StubGenericMonitorProvider(),
        mode=RunMode.MOCK_AGENT,
        fixture=fixture,
        expectations=expectations,
        project_profile=profile,
    )

    assert summary.comparison_valid is True
    assert summary.fallback_used is False
    assert summary.event_count == 2
    assert summary.regression.passed is True
    assert summary.regression.decision_accuracy == 1.0
    assert summary.total_tokens == 0
