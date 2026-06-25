from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from signal_harness.signal.schemas import SignalEvent


ROOT = Path(__file__).resolve().parents[2]


def test_versioned_configs_are_parseable() -> None:
    for name in ("project_profile.yaml", "watchlist.yaml", "signal_policy.yaml"):
        payload = yaml.safe_load((ROOT / "configs" / name).read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
        assert payload


def test_score_weights_sum_to_one() -> None:
    policy = yaml.safe_load(
        (ROOT / "configs" / "signal_policy.yaml").read_text(encoding="utf-8")
    )
    assert sum(policy["score_weights"].values()) == pytest.approx(1.0)


def test_sample_events_match_signal_event_schema() -> None:
    payload = json.loads(
        (ROOT / "examples" / "signal_harness" / "sample_events.json").read_text(
            encoding="utf-8"
        )
    )
    events = [SignalEvent.model_validate(item) for item in payload]

    assert len(events) >= 4
    assert any(event.event_id == "demo-001" for event in events)


def test_notice_preserves_openharness_attribution() -> None:
    notice = (ROOT / "NOTICE.md").read_text(encoding="utf-8")
    assert "HKUDS/OpenHarness" in notice
    assert (ROOT / "LICENSE").exists()
