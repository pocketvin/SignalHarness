from __future__ import annotations

import asyncio
import json
from pathlib import Path

from signal_harness.agent_integration.mode import RunMode
from signal_harness.capability_eval import (
    load_capability_suite,
    run_capability_comparison,
    write_capability_comparison,
)
from signal_harness.narrative_calibration import (
    export_blind_narrative_pairs,
    load_narrative_seed,
    load_review_file,
    narrative_calibration_status,
)
from signal_harness.providers.mock_provider import MockProvider
from signal_harness.signal.policy import load_signal_policy, load_yaml_mapping


def _export(project_root: Path, tmp_path: Path):
    suite = load_capability_suite(
        project_root / "examples/signal_harness/capability_golden_v1.json"
    )
    summary = asyncio.run(
        run_capability_comparison(
            provider=MockProvider(),
            mode=RunMode.MOCK_AGENT,
            suite=suite,
            project_profile=load_yaml_mapping(project_root / "configs/project_profile.yaml"),
            policy=load_signal_policy(project_root / "configs/signal_policy.yaml"),
            trials=1,
        )
    )
    capability_paths = write_capability_comparison(tmp_path / "capability", summary)
    paths = export_blind_narrative_pairs(
        capability_summary_path=capability_paths["json"],
        capability_suite=suite,
        seed=load_narrative_seed(
            project_root / "examples/signal_harness/narrative_calibration_seed.json"
        ),
        output_dir=tmp_path / "narrative",
    )
    return paths


def test_blind_narrative_export_separates_variant_identity(
    project_root: Path, tmp_path: Path
) -> None:
    paths = _export(project_root, tmp_path)
    review_text = paths["review"].read_text(encoding="utf-8")
    mapping = json.loads(paths["mapping"].read_text(encoding="utf-8"))
    review = load_review_file(paths["review"])

    assert len(review.pairs) == 16
    assert review.calibration_eligible is False
    assert "shared-evidence-single-agent" not in review_text
    assert "split-impact-action-narrative" not in review_text
    mapped = {
        item[side]
        for item in mapping["mappings"]
        for side in ("A_variant", "B_variant")
    }
    assert mapped == {
        "shared-evidence-single-agent",
        "split-impact-action-narrative",
    }
    assert all(pair.human_preference is None for pair in review.pairs)
    assert all(
        set(pair.dimension_preferences) == set(review.rubric_dimensions)
        for pair in review.pairs
    )


def test_mock_pairs_can_never_calibrate_production_judge(
    project_root: Path, tmp_path: Path
) -> None:
    review = load_review_file(_export(project_root, tmp_path)["review"])
    for pair in review.pairs:
        pair.human_preference = "A"
    status = narrative_calibration_status(review)

    assert status.labeled_pairs == 16
    assert status.calibration_eligible is False
    assert status.ready_for_judge_calibration is False
    assert status.judge_calibrated is False
    assert status.judge_enabled is False


def test_enough_real_human_labels_only_make_judge_calibration_ready(
    project_root: Path, tmp_path: Path
) -> None:
    review = load_review_file(_export(project_root, tmp_path)["review"])
    review.calibration_eligible = True
    review.source_mode = "agent"
    for pair in review.pairs[:15]:
        pair.human_preference = "A"
        pair.dimension_preferences["factual_grounding"] = "A"
    status = narrative_calibration_status(review)

    assert status.labeled_pairs == 15
    assert status.ready_for_judge_calibration is True
    # Human labels only unlock a future calibration experiment; they do not silently
    # turn on an LLM judge or claim agreement/bias checks have passed.
    assert status.judge_calibrated is False
    assert status.judge_enabled is False
    assert "agreement/bias" in status.blocker
