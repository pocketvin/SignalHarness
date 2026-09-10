"""Build auditable calibration episodes and replay candidate ranking policies."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from signal_harness.alerts import AlertPolicy, select_alerts
from signal_harness.persistence import ChangeLedger
from signal_harness.signal.policy import decision_for_score
from signal_harness.signal.scorer import score_signal
from signal_harness.signal.schemas import SignalAssessment, SignalEvent

DATASET_VERSION = "calibration-episodes-v1"
EpisodeLabel = Literal["positive", "negative", "ambiguous", "unlabeled"]
ReplayRecommendation = Literal[
    "insufficient_evidence",
    "review_and_consider",
    "reject_regression",
    "reject_no_gain",
]

_POSITIVE_FEEDBACK = {"useful", "missed_signal"}
_NEGATIVE_FEEDBACK = {"not_useful", "false_positive", "too_generic"}


class CalibrationEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: str
    project_id: str
    scan_id: str
    change_id: str
    event_revision_id: int
    event: SignalEvent
    assessment: SignalAssessment | None = None
    feedback: list[dict[str, Any]] = Field(default_factory=list)
    outcomes: list[dict[str, Any]] = Field(default_factory=list)
    relevance_label: EpisodeLabel
    label_evidence: list[str] = Field(default_factory=list)


class CalibrationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = DATASET_VERSION
    project_id: str
    episodes: list[CalibrationEpisode] = Field(default_factory=list)
    positive_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    ambiguous_count: int = Field(ge=0)
    unlabeled_count: int = Field(ge=0)
    orphan_feedback_count: int = Field(ge=0)
    feedback_count: int = Field(ge=0)
    outcome_count: int = Field(ge=0)

    @property
    def labeled_count(self) -> int:
        return self.positive_count + self.negative_count


class ReplayMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    missed_positive: int = Field(ge=0)
    true_negative: int = Field(ge=0)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)


class CalibrationShadowChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: str
    scan_id: str
    change_id: str
    event_revision_id: int
    relevance_label: EpisodeLabel
    old_score: float = Field(ge=0, le=100)
    proposed_score: float = Field(ge=0, le=100)
    score_delta: float
    old_rank: int = Field(ge=1)
    proposed_rank: int = Field(ge=1)
    old_decision: str
    proposed_decision: str
    old_notification_eligible: bool | None = None
    proposed_notification_eligible: bool | None = None
    ranking_changed: bool
    decision_changed: bool
    notification_changed: bool


class CalibrationReplayReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    project_id: str
    labeled_count: int = Field(ge=0)
    minimum_labeled_required: int = Field(ge=1)
    old_metrics: ReplayMetrics
    proposed_metrics: ReplayMetrics
    false_positive_reduction: int
    missed_positive_reduction: int
    ranking_change_count: int = Field(ge=0)
    decision_change_count: int = Field(ge=0)
    notification_change_count: int = Field(ge=0)
    shadow_changes: list[CalibrationShadowChange] = Field(default_factory=list)
    recommendation: ReplayRecommendation
    promotion_allowed: bool
    reasons: list[str] = Field(default_factory=list)


def _episode_key(scan_id: str, change_id: str, revision_id: int) -> str:
    raw = f"{scan_id}|{change_id}|{revision_id}"
    return "episode-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _episode_label(
    feedback: list[dict[str, Any]], outcomes: list[dict[str, Any]]
) -> tuple[EpisodeLabel, list[str]]:
    positive: list[str] = []
    negative: list[str] = []
    for item in feedback:
        label = str(item.get("label") or "")
        if label in _POSITIVE_FEEDBACK:
            positive.append(f"feedback:{label}")
        elif label in _NEGATIVE_FEEDBACK:
            negative.append(f"feedback:{label}")
    for item in outcomes:
        observed = item.get("impact_observed")
        if observed is True:
            positive.append("outcome:impact_observed")
        elif observed is False:
            negative.append("outcome:no_impact")
    evidence = [*positive, *negative]
    if positive and negative:
        return "ambiguous", evidence
    if positive:
        return "positive", evidence
    if negative:
        return "negative", evidence
    return "unlabeled", evidence


def build_calibration_dataset(
    *, ledger: ChangeLedger, project_id: str
) -> CalibrationDataset:
    """Project durable user feedback/outcomes onto frozen ScanChange episodes."""

    feedback = ledger.list_calibration_feedback(project_id=project_id)
    outcomes = ledger.list_outcomes(project_id=project_id)
    feedback_by_key: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    outcomes_by_key: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    orphan_feedback = 0
    for item in feedback:
        scan_id = item.get("scan_id")
        change_id = item.get("change_id")
        revision = item.get("event_revision_id")
        if not scan_id or not change_id or revision is None:
            orphan_feedback += 1
            continue
        feedback_by_key[(str(scan_id), str(change_id), int(revision))].append(item)
    for item in outcomes:
        outcomes_by_key[
            (str(item["scan_id"]), str(item["change_id"]), int(item["event_revision_id"]))
        ].append(item)

    keys = sorted(set(feedback_by_key) | set(outcomes_by_key))
    episodes: list[CalibrationEpisode] = []
    for scan_id, change_id, revision_id in keys:
        frozen = ledger.scan_change(scan_id=scan_id, change_id=change_id)
        if frozen is None or int(frozen["event_revision_id"]) != revision_id:
            continue
        event = SignalEvent.model_validate(frozen["event"])
        assessment_raw = frozen.get("assessment")
        assessment = (
            SignalAssessment.model_validate(assessment_raw)
            if isinstance(assessment_raw, dict)
            else None
        )
        episode_feedback = feedback_by_key[(scan_id, change_id, revision_id)]
        episode_outcomes = outcomes_by_key[(scan_id, change_id, revision_id)]
        label, evidence = _episode_label(episode_feedback, episode_outcomes)
        episodes.append(
            CalibrationEpisode(
                episode_id=_episode_key(scan_id, change_id, revision_id),
                project_id=project_id,
                scan_id=scan_id,
                change_id=change_id,
                event_revision_id=revision_id,
                event=event,
                assessment=assessment,
                feedback=episode_feedback,
                outcomes=episode_outcomes,
                relevance_label=label,
                label_evidence=evidence,
            )
        )

    counts = {label: 0 for label in ("positive", "negative", "ambiguous", "unlabeled")}
    for episode in episodes:
        counts[episode.relevance_label] += 1
    return CalibrationDataset(
        project_id=project_id,
        episodes=episodes,
        positive_count=counts["positive"],
        negative_count=counts["negative"],
        ambiguous_count=counts["ambiguous"],
        unlabeled_count=counts["unlabeled"],
        orphan_feedback_count=orphan_feedback,
        feedback_count=len(feedback),
        outcome_count=len(outcomes),
    )


def _metrics(
    episodes: list[CalibrationEpisode],
    *,
    project_profile: dict[str, Any],
    policy: dict[str, Any],
) -> ReplayMetrics:
    threshold = float(policy.get("thresholds", {}).get("save", 45))
    tp = fp = missed = tn = 0
    for episode in episodes:
        if episode.relevance_label not in {"positive", "negative"}:
            continue
        predicted = (
            score_signal(episode.event, project_profile, policy).final_score >= threshold
        )
        if episode.relevance_label == "positive":
            if predicted:
                tp += 1
            else:
                missed += 1
        elif predicted:
            fp += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + missed) if tp + missed else 0.0
    return ReplayMetrics(
        true_positive=tp,
        false_positive=fp,
        missed_positive=missed,
        true_negative=tn,
        precision=round(precision, 4),
        recall=round(recall, 4),
    )


def _projected_notification_eligible(
    episode: CalibrationEpisode,
    *,
    score: float,
    policy: dict[str, Any],
) -> bool | None:
    if episode.assessment is None:
        return None
    decision = decision_for_score(score, policy)
    projected = episode.assessment.model_copy(
        update={
            "decision": decision,
            "impact_score": score,
        }
    )
    return bool(
        select_alerts(
            [episode.event],
            [projected],
            policy=AlertPolicy.from_signal_policy(policy),
        )
    )


def _shadow_changes(
    episodes: list[CalibrationEpisode],
    *,
    project_profile: dict[str, Any],
    old_policy: dict[str, Any],
    proposed_policy: dict[str, Any],
) -> list[CalibrationShadowChange]:
    old_scores = {
        episode.episode_id: score_signal(
            episode.event, project_profile, old_policy
        ).final_score
        for episode in episodes
    }
    proposed_scores = {
        episode.episode_id: score_signal(
            episode.event, project_profile, proposed_policy
        ).final_score
        for episode in episodes
    }
    old_order = sorted(
        episodes,
        key=lambda item: (-old_scores[item.episode_id], item.episode_id),
    )
    proposed_order = sorted(
        episodes,
        key=lambda item: (-proposed_scores[item.episode_id], item.episode_id),
    )
    old_ranks = {item.episode_id: index for index, item in enumerate(old_order, start=1)}
    proposed_ranks = {
        item.episode_id: index for index, item in enumerate(proposed_order, start=1)
    }
    result: list[CalibrationShadowChange] = []
    for episode in episodes:
        old_score = old_scores[episode.episode_id]
        proposed_score = proposed_scores[episode.episode_id]
        old_decision = decision_for_score(old_score, old_policy).value
        proposed_decision = decision_for_score(proposed_score, proposed_policy).value
        old_notification = _projected_notification_eligible(
            episode, score=old_score, policy=old_policy
        )
        proposed_notification = _projected_notification_eligible(
            episode, score=proposed_score, policy=proposed_policy
        )
        notification_changed = (
            old_notification is not None
            and proposed_notification is not None
            and old_notification != proposed_notification
        )
        result.append(
            CalibrationShadowChange(
                episode_id=episode.episode_id,
                scan_id=episode.scan_id,
                change_id=episode.change_id,
                event_revision_id=episode.event_revision_id,
                relevance_label=episode.relevance_label,
                old_score=old_score,
                proposed_score=proposed_score,
                score_delta=round(proposed_score - old_score, 2),
                old_rank=old_ranks[episode.episode_id],
                proposed_rank=proposed_ranks[episode.episode_id],
                old_decision=old_decision,
                proposed_decision=proposed_decision,
                old_notification_eligible=old_notification,
                proposed_notification_eligible=proposed_notification,
                ranking_changed=(
                    old_ranks[episode.episode_id] != proposed_ranks[episode.episode_id]
                ),
                decision_changed=old_decision != proposed_decision,
                notification_changed=notification_changed,
            )
        )
    return sorted(
        result,
        key=lambda item: (
            not (item.notification_changed or item.decision_changed or item.ranking_changed),
            item.proposed_rank,
            item.episode_id,
        ),
    )


def evaluate_calibration_replay(
    dataset: CalibrationDataset,
    *,
    project_profile: dict[str, Any],
    old_policy: dict[str, Any],
    proposed_policy: dict[str, Any],
    minimum_labeled_required: int = 3,
) -> CalibrationReplayReport:
    """Replay one candidate without allowing no-data or no-gain promotion."""

    if minimum_labeled_required < 1:
        raise ValueError("minimum_labeled_required must be positive")
    old_metrics = _metrics(dataset.episodes, project_profile=project_profile, policy=old_policy)
    proposed_metrics = _metrics(
        dataset.episodes, project_profile=project_profile, policy=proposed_policy
    )
    fp_reduction = old_metrics.false_positive - proposed_metrics.false_positive
    missed_reduction = old_metrics.missed_positive - proposed_metrics.missed_positive
    shadow = _shadow_changes(
        dataset.episodes,
        project_profile=project_profile,
        old_policy=old_policy,
        proposed_policy=proposed_policy,
    )
    ranking_change_count = sum(item.ranking_changed for item in shadow)
    decision_change_count = sum(item.decision_changed for item in shadow)
    notification_change_count = sum(item.notification_changed for item in shadow)
    labeled = dataset.labeled_count
    reasons: list[str] = []
    if labeled < minimum_labeled_required:
        recommendation: ReplayRecommendation = "insufficient_evidence"
        promotion = False
        reasons.append(
            f"only {labeled} labeled episodes; require {minimum_labeled_required}"
        )
    else:
        regression = (
            fp_reduction < 0
            or missed_reduction < 0
            or proposed_metrics.precision < old_metrics.precision
            or proposed_metrics.recall < old_metrics.recall
        )
        improved = (
            fp_reduction > 0
            or missed_reduction > 0
            or proposed_metrics.precision > old_metrics.precision
            or proposed_metrics.recall > old_metrics.recall
        )
        if regression:
            recommendation = "reject_regression"
            promotion = False
            reasons.append("candidate regresses at least one guarded replay metric")
        elif not improved:
            recommendation = "reject_no_gain"
            promotion = False
            reasons.append("candidate shows no measured improvement on labeled episodes")
        else:
            recommendation = "review_and_consider"
            promotion = True
            reasons.append("candidate improves replay metrics without a guarded regression")
    if dataset.ambiguous_count:
        reasons.append(
            f"{dataset.ambiguous_count} ambiguous episode(s) excluded from hard replay labels"
        )
    if dataset.orphan_feedback_count:
        reasons.append(
            f"{dataset.orphan_feedback_count} feedback record(s) lack a frozen Change attachment"
        )
    return CalibrationReplayReport(
        dataset_version=dataset.version,
        project_id=dataset.project_id,
        labeled_count=labeled,
        minimum_labeled_required=minimum_labeled_required,
        old_metrics=old_metrics,
        proposed_metrics=proposed_metrics,
        false_positive_reduction=fp_reduction,
        missed_positive_reduction=missed_reduction,
        ranking_change_count=ranking_change_count,
        decision_change_count=decision_change_count,
        notification_change_count=notification_change_count,
        shadow_changes=shadow,
        recommendation=recommendation,
        promotion_allowed=promotion,
        reasons=reasons,
    )
