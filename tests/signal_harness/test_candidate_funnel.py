from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from signal_harness.signal.candidates import select_candidates
from signal_harness.signal.normalizer import normalize_event
from signal_harness.signal.policy import load_signal_policy, load_yaml_mapping


def test_candidate_funnel_keeps_older_project_relevant_signal_over_recent_noise(
    project_root: Path,
) -> None:
    profile = load_yaml_mapping(project_root / "configs/project_profile.yaml")
    policy = load_signal_policy(project_root / "configs/signal_policy.yaml")
    now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    events = []
    for index in range(12):
        events.append(
            normalize_event(
                {
                    "source_type": "github_issue",
                    "source_name": f"unrelated/repo-{index}",
                    "title": f"Minor UI wording cleanup {index}",
                    "content": "Cosmetic copy change only.",
                    "url": f"https://example.test/noise/{index}",
                    "published_at": (now - timedelta(minutes=index)).isoformat(),
                    "collected_at": now.isoformat(),
                    "raw_payload": {"source_authority": "community"},
                }
            )
        )
    relevant = normalize_event(
        {
            "source_type": "github_issue",
            "source_name": "langchain-ai/langgraph",
            "title": "Checkpoint persistence regression loses accepted runs after restart",
            "content": "Durability and recovery semantics regress after a process restart.",
            "url": "https://github.com/langchain-ai/langgraph/issues/99999",
            "published_at": (now - timedelta(days=5)).isoformat(),
            "collected_at": now.isoformat(),
            "raw_payload": {"source_authority": "maintainer"},
        }
    )
    events.append(relevant)

    result = select_candidates(
        events,
        project_profile=profile,
        policy=policy,
        max_events=4,
        max_events_per_source=2,
        now=now,
    )

    assert relevant.event_id in {event.event_id for event in result.events}
    assert result.metadata["before_count"] == 13
    assert result.metadata["after_count"] == 4
    assert result.metadata["strategy"] == "project_relevance_v2"
