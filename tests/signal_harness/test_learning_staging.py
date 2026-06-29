from __future__ import annotations

from copy import deepcopy
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from signal_harness import cli as cli_module
from signal_harness.agent_integration.schemas import LearningPolicyOutput, ReplayEvaluation
from signal_harness.cli import app
from signal_harness.learning import (
    ProposalRiskClassifier,
    apply_staged_learning,
    load_learning_staging,
    stage_learning_proposal,
)
from signal_harness.signal.policy import load_signal_policy
from signal_harness.signal.schemas import (
    FeedbackLabel,
    FeedbackRecord,
    PolicyUpdateProposal,
)

runner = CliRunner()


def _copy_configs(project_root: Path, tmp_path: Path) -> Path:
    config = tmp_path / "configs"
    shutil.copytree(project_root / "configs", config)
    return config


def _replay(
    *,
    recommendation: str = "review-and-consider",
    false_positive_reduction: int = 0,
    missed_signal_reduction: int = 0,
) -> ReplayEvaluation:
    return ReplayEvaluation(
        old_precision_proxy=0.8,
        new_precision_proxy=0.8,
        false_positive_reduction=false_positive_reduction,
        missed_signal_reduction=missed_signal_reduction,
        recommendation=recommendation,
    )


def _learning(
    policy: dict[str, object],
    *,
    new_policy: dict[str, object] | None = None,
    watchlist: dict[str, object] | None = None,
    skill: str = "Memory summary notes only.",
    proposal_id: str = "proposal-low",
) -> LearningPolicyOutput:
    return LearningPolicyOutput(
        policy_update_proposal=PolicyUpdateProposal(
            proposal_id=proposal_id,
            reason="Review learning notes.",
            old_policy=deepcopy(policy),
            new_policy=deepcopy(new_policy or policy),
            expected_effect="Review-only staged change.",
            requires_approval=True,
        ),
        skill_update_proposal=skill,
        watchlist_update_proposal=watchlist
        or {"requires_approval": True, "suggested_changes": []},
        learning_summary="Stage proposal for review.",
        memory_sections_read=["FeedbackMemory"],
    )


def _write_feedback(state: Path, note: str = "checkpoint memory signal") -> None:
    state.mkdir(parents=True, exist_ok=True)
    record = FeedbackRecord(
        event_id="demo-001",
        feedback=FeedbackLabel.USEFUL,
        note=note,
        created_at="2026-06-29T00:00:00+00:00",
    )
    (state / "feedback_memory.json").write_text(
        json.dumps([record.model_dump(mode="json")]),
        encoding="utf-8",
    )


def test_stage_low_risk_learning_writes_state_and_outputs(
    project_root: Path,
    tmp_path: Path,
) -> None:
    config = _copy_configs(project_root, tmp_path)
    policy = load_signal_policy(config / "signal_policy.yaml")

    staged = stage_learning_proposal(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "outputs",
        learning=_learning(policy),
        replay=_replay(),
    )

    assert staged.status == "staged"
    assert staged.risk.risk_level == "low"
    assert staged.risk.replay_gate_passed is True
    assert (tmp_path / "state/learning_staging.json").exists()
    assert (tmp_path / "outputs/latest_learning_staging.json").exists()
    assert (tmp_path / "outputs/latest_learning_risk_report.md").exists()


def test_risk_classifier_flags_threshold_changes_as_high(
    project_root: Path,
    tmp_path: Path,
) -> None:
    config = _copy_configs(project_root, tmp_path)
    policy = load_signal_policy(config / "signal_policy.yaml")
    new_policy = deepcopy(policy)
    new_policy["thresholds"] = {
        "save": 10,
        "alert": 30,
        "action_required": 50,
    }

    report = ProposalRiskClassifier().classify(
        _learning(policy, new_policy=new_policy),
        _replay(),
    )

    assert report.risk_level == "high"
    assert any("threshold" in reason for reason in report.reasons)


def test_no_replay_can_stage_but_cannot_apply(
    project_root: Path,
    tmp_path: Path,
) -> None:
    config = _copy_configs(project_root, tmp_path)
    policy = load_signal_policy(config / "signal_policy.yaml")
    staged = stage_learning_proposal(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "outputs",
        learning=_learning(policy, proposal_id="proposal-no-replay"),
        replay=None,
    )

    assert staged.status == "staged"
    assert staged.risk.replay_gate_passed is False
    with pytest.raises(PermissionError, match="Replay gate"):
        apply_staged_learning(
            state_dir=tmp_path / "state",
            config_dir=config,
            proposal_id="proposal-no-replay",
            yes=True,
        )


def test_low_risk_replay_passed_apply_requires_yes_and_records_change(
    project_root: Path,
    tmp_path: Path,
) -> None:
    config = _copy_configs(project_root, tmp_path)
    policy = load_signal_policy(config / "signal_policy.yaml")
    stage_learning_proposal(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "outputs",
        learning=_learning(policy, proposal_id="proposal-apply"),
        replay=_replay(),
    )

    with pytest.raises(PermissionError, match="requires --yes"):
        apply_staged_learning(
            state_dir=tmp_path / "state",
            config_dir=config,
            proposal_id="proposal-apply",
            yes=False,
        )
    applied = apply_staged_learning(
        state_dir=tmp_path / "state",
        config_dir=config,
        proposal_id="proposal-apply",
        yes=True,
    )

    assert applied == config / "signal_policy.yaml"
    proposals = load_learning_staging(tmp_path / "state")
    assert proposals[0].status == "applied"
    applied_log = json.loads(
        (tmp_path / "state/applied_learning_changes.json").read_text(
            encoding="utf-8"
        )
    )
    assert applied_log["applied"][0]["proposal_id"] == "proposal-apply"


def test_learning_apply_cli_requires_yes(
    project_root: Path,
    tmp_path: Path,
) -> None:
    config = _copy_configs(project_root, tmp_path)
    policy = load_signal_policy(config / "signal_policy.yaml")
    stage_learning_proposal(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "outputs",
        learning=_learning(policy, proposal_id="proposal-cli-apply"),
        replay=_replay(),
    )

    result = runner.invoke(
        app,
        [
            "learning-apply",
            "--proposal-id",
            "proposal-cli-apply",
            "--cwd",
            str(project_root),
            "--config-dir",
            str(config),
            "--state-dir",
            str(tmp_path / "state"),
        ],
    )

    assert result.exit_code != 0
    assert load_signal_policy(config / "signal_policy.yaml") == policy


def test_replay_reject_blocks_apply(
    project_root: Path,
    tmp_path: Path,
) -> None:
    config = _copy_configs(project_root, tmp_path)
    policy = load_signal_policy(config / "signal_policy.yaml")
    stage_learning_proposal(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "outputs",
        learning=_learning(policy, proposal_id="proposal-reject"),
        replay=_replay(recommendation="reject-or-revise"),
    )

    with pytest.raises(PermissionError, match="Replay gate"):
        apply_staged_learning(
            state_dir=tmp_path / "state",
            config_dir=config,
            proposal_id="proposal-reject",
            yes=True,
        )


def test_high_risk_proposal_cannot_apply_noninteractively(
    project_root: Path,
    tmp_path: Path,
) -> None:
    config = _copy_configs(project_root, tmp_path)
    policy = load_signal_policy(config / "signal_policy.yaml")
    new_policy = deepcopy(policy)
    new_policy["permission_policy"] = {"auto_allow": ["modify_signal_policy"]}
    stage_learning_proposal(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "outputs",
        learning=_learning(
            policy,
            new_policy=new_policy,
            proposal_id="proposal-high",
        ),
        replay=_replay(),
    )

    with pytest.raises(PermissionError, match="Only low-risk"):
        apply_staged_learning(
            state_dir=tmp_path / "state",
            config_dir=config,
            proposal_id="proposal-high",
            yes=True,
        )


def test_learning_stage_and_review_cli(
    project_root: Path,
    tmp_path: Path,
) -> None:
    config = _copy_configs(project_root, tmp_path)
    policy = load_signal_policy(config / "signal_policy.yaml")
    state = tmp_path / "state"
    outputs = tmp_path / "outputs"
    state.mkdir()
    outputs.mkdir()
    learning = _learning(policy, proposal_id="proposal-cli")
    (state / "latest_learning_observation.json").write_text(
        json.dumps(
            {
                "learning_summary": learning.learning_summary,
                "memory_sections_read": learning.memory_sections_read,
                "policy_update_proposal": learning.policy_update_proposal.model_dump(
                    mode="json"
                ),
                "watchlist_update_proposal": learning.watchlist_update_proposal,
                "skill_update_proposal": learning.skill_update_proposal,
            }
        ),
        encoding="utf-8",
    )
    (state / "replay_evaluation.json").write_text(
        _replay().model_dump_json(),
        encoding="utf-8",
    )

    staged = runner.invoke(
        app,
        [
            "learning-stage",
            "--cwd",
            str(project_root),
            "--state-dir",
            str(state),
            "--output-dir",
            str(outputs),
        ],
    )
    reviewed = runner.invoke(
        app,
        [
            "learning-review",
            "--cwd",
            str(project_root),
            "--state-dir",
            str(state),
        ],
    )

    assert staged.exit_code == 0, staged.output
    assert "proposal-cli" in staged.output
    assert reviewed.exit_code == 0, reviewed.output
    assert "risk=low" in reviewed.output


def test_calibrate_apply_yes_blocks_replay_rejected_proposal(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _copy_configs(project_root, tmp_path)
    policy = load_signal_policy(config / "signal_policy.yaml")
    state = tmp_path / "state"
    _write_feedback(state)
    monkeypatch.setattr(
        cli_module,
        "evaluate_policy_replay",
        lambda *args, **kwargs: _replay(recommendation="reject-or-revise"),
    )

    result = runner.invoke(
        app,
        [
            "calibrate",
            "--apply",
            "--yes",
            "--cwd",
            str(project_root),
            "--config-dir",
            str(config),
            "--state-dir",
            str(state),
        ],
    )

    assert result.exit_code != 0
    assert "Replay gate did not pass" in result.output
    assert load_signal_policy(config / "signal_policy.yaml") == policy


def test_calibrate_apply_yes_blocks_high_risk_proposal(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _copy_configs(project_root, tmp_path)
    policy = load_signal_policy(config / "signal_policy.yaml")
    state = tmp_path / "state"

    class HighRiskLearningAgent:
        def fallback(self, memories: dict[str, object]) -> LearningPolicyOutput:
            policy_memory = memories["policy_memory"]
            assert isinstance(policy_memory, dict)
            active_policy = dict(policy_memory["active_policy"])
            new_policy = deepcopy(active_policy)
            new_policy["thresholds"] = {
                "save": 10,
                "alert": 20,
                "action_required": 30,
            }
            return _learning(
                active_policy,
                new_policy=new_policy,
                proposal_id="proposal-high-cli",
            )

    monkeypatch.setattr(cli_module, "LearningPolicyAgent", HighRiskLearningAgent)

    result = runner.invoke(
        app,
        [
            "calibrate",
            "--apply",
            "--yes",
            "--cwd",
            str(project_root),
            "--config-dir",
            str(config),
            "--state-dir",
            str(state),
        ],
    )

    assert result.exit_code != 0
    assert "Only low-risk proposals can be applied" in result.output
    assert load_signal_policy(config / "signal_policy.yaml") == policy
