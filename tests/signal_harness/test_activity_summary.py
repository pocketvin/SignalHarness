from signal_harness.intelligence.activity_summary import summarize_project_activity


def _item(change_id: str, title: str, kind: str = "github_commit", date: str = "2026-09-12T00:00:00Z"):
    return {
        "change_id": change_id,
        "title": title,
        "summary": title,
        "kind": kind,
        "published_at": date,
        "corpus_role": "project_activity",
    }


def test_activity_summary_groups_conventional_commits_into_product_work_areas() -> None:
    items = [
        _item("c1", "fix(ui): recover chat loading (#10)", date="2026-09-12T03:00:00Z"),
        _item("c2", "refactor(control-ui): share picker state (#11)", date="2026-09-12T02:00:00Z"),
        _item("c3", "fix(agents): show provider rejection details (#12)"),
        _item("c4", "perf(models): reuse catalog identity index (#13)"),
        _item("c5", "test(sdk): expect worker environment profiles (#14)"),
        _item("c6", "docs: clarify install path (#15)"),
    ]

    summary = summarize_project_activity(items, max_groups=6)

    assert summary["total_count"] == 6
    labels = {group["label"]: group for group in summary["groups"]}
    assert labels["前端 / UI"]["count"] == 2
    assert labels["Agent / 执行"]["count"] == 1
    assert labels["模型 / Provider"]["count"] == 1
    assert labels["SDK / API"]["count"] == 1
    assert labels["前端 / UI"]["samples"][0]["text"] == "recover chat loading"
    assert "(#10)" not in labels["前端 / UI"]["samples"][0]["text"]
    assert summary["other_count"] == 0

    compact = summarize_project_activity(items, max_groups=4)
    assert len(compact["groups"]) == 4
    assert compact["other_count"] == 1


def test_activity_summary_falls_back_to_source_kind_without_inventing_semantics() -> None:
    summary = summarize_project_activity(
        [_item("c1", "Release 2026.9", kind="github_release")]
    )
    assert summary["groups"] == [
        {
            "label": "版本发布",
            "count": 1,
            "samples": [
                {
                    "change_id": "c1",
                    "text": "Release 2026.9",
                    "published_at": "2026-09-12T00:00:00Z",
                    "kind": "github_release",
                }
            ],
        }
    ]


def test_project_activity_deep_dive_is_rejected_before_any_context_or_model_work(
    project_root, tmp_path, monkeypatch
) -> None:
    import asyncio

    import pytest

    from signal_harness.intelligence.deep_dive import (
        DeepDiveManager,
        ProjectActivityDeepDiveUnsupported,
    )

    class FakeRepository:
        def change(self, scan_id: str, change_id: str):
            assert scan_id == "scan-activity"
            assert change_id == "chg-activity"
            return {"corpus_role": "project_activity"}

        def profile_for_scan(self, scan_id: str):
            raise AssertionError("project activity rejection must happen before context loading")

    manager = DeepDiveManager(project_root / "configs", tmp_path / "state")
    monkeypatch.setattr(manager, "repository", lambda project_id: FakeRepository())

    with pytest.raises(ProjectActivityDeepDiveUnsupported):
        asyncio.run(manager.start("signalharness", "scan-activity", "chg-activity"))
