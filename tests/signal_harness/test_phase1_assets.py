from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from signal_harness.signal.schemas import SignalEvent


ROOT = Path(__file__).resolve().parents[2]


def test_versioned_configs_are_parseable() -> None:
    for name in (
        "project_profile.yaml",
        "watchlist.yaml",
        "watchlist_demo.yaml",
        "signal_policy.yaml",
    ):
        payload = yaml.safe_load((ROOT / "configs" / name).read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
        assert payload


def test_live_watchlist_does_not_use_sample_fixture() -> None:
    live = yaml.safe_load((ROOT / "configs/watchlist.yaml").read_text(encoding="utf-8"))
    demo = yaml.safe_load(
        (ROOT / "configs/watchlist_demo.yaml").read_text(encoding="utf-8")
    )

    assert "sample_events.json" not in json.dumps(live)
    assert "sample_events.json" in json.dumps(demo)


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


def test_curated_showcase_events_match_signal_event_schema() -> None:
    payload = json.loads(
        (
            ROOT / "examples" / "signal_harness" / "curated_showcase_events.json"
        ).read_text(encoding="utf-8")
    )
    events = [SignalEvent.model_validate(item) for item in payload]

    assert len(events) >= 8
    assert {event.source_type for event in events} >= {
        "github_release",
        "github_issue",
        "rss",
        "web_change",
    }
    assert not any("example.com" in event.url for event in events)


def test_notice_describes_independent_harness_identity() -> None:
    notice = (ROOT / "NOTICE.md").read_text(encoding="utf-8")
    assert "independent project inspired by general agent harness design patterns" in notice
    assert "does not vendor or depend on OpenHarness code" in notice
    assert not (ROOT / "LICENSE").exists()
