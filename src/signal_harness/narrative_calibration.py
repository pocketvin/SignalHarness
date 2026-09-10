"""Blind human A/B calibration scaffolding for future Narrative LLM judges."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from signal_harness.capability_eval import CapabilityComparisonSummary, CapabilitySuite
from signal_harness.utils.fs import atomic_write_text

HumanPreference = Literal["A", "B", "tie", "invalid"]
DimensionPreference = Literal["A", "B", "tie", "not_applicable"]


class NarrativeCalibrationSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    description: str = ""
    minimum_labeled_pairs: int = Field(default=15, ge=1)
    rubric_dimensions: list[str] = Field(min_length=1)
    case_ids: list[str] = Field(min_length=1)


class NarrativeOutputForReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    impact_score: float = Field(ge=0, le=100)
    what_changed_zh: str
    why_relevant_zh: str
    recommended_actions_zh: list[str] = Field(default_factory=list)


class NarrativeReviewPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_id: str
    case_id: str
    case_title: str
    truth_status: str
    A: NarrativeOutputForReview
    B: NarrativeOutputForReview
    human_preference: HumanPreference | None = None
    dimension_preferences: dict[str, DimensionPreference | None] = Field(default_factory=dict)
    human_note: str = ""


class NarrativeReviewFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    capability_suite: str
    source_mode: str
    source_provider: str
    source_model: str
    calibration_eligible: bool
    rubric_dimensions: list[str]
    minimum_labeled_pairs: int
    pairs: list[NarrativeReviewPair]


class NarrativePairMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_id: str
    case_id: str
    A_variant: str
    B_variant: str


class NarrativeMappingFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    capability_suite: str
    mappings: list[NarrativePairMapping]


class NarrativeCalibrationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    pair_count: int = Field(ge=0)
    labeled_pairs: int = Field(ge=0)
    label_coverage: float = Field(ge=0, le=1)
    dimension_label_counts: dict[str, int] = Field(default_factory=dict)
    calibration_eligible: bool
    minimum_labeled_pairs: int = Field(ge=1)
    ready_for_judge_calibration: bool
    judge_calibrated: bool = False
    judge_enabled: bool = False
    blocker: str


def load_narrative_seed(path: str | Path) -> NarrativeCalibrationSeed:
    return NarrativeCalibrationSeed.model_validate(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )


def load_review_file(path: str | Path) -> NarrativeReviewFile:
    return NarrativeReviewFile.model_validate(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )


def _pair_id(case_id: str) -> str:
    return "narrative-pair-" + hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:12]


def _left_is_first_variant(case_id: str) -> bool:
    # Stable blind side assignment prevents a systematic left/right bias while keeping
    # reruns reproducible. Variant identities live only in the separate mapping file.
    digest = hashlib.sha256(f"narrative-side|{case_id}".encode("utf-8")).digest()
    return digest[0] % 2 == 0


def export_blind_narrative_pairs(
    *,
    capability_summary_path: Path,
    capability_suite: CapabilitySuite,
    seed: NarrativeCalibrationSeed,
    output_dir: Path,
) -> dict[str, Path]:
    summary = CapabilityComparisonSummary.model_validate(
        json.loads(capability_summary_path.read_text(encoding="utf-8"))
    )
    if summary.suite != capability_suite.suite:
        raise ValueError("capability summary and Capability Golden suite do not match")
    if len(summary.variants) != 2:
        raise ValueError("narrative pair export currently requires exactly two variants")
    variants = summary.variants
    outputs_by_variant = {
        item.variant: {output.case_id: output for output in item.observed_outputs}
        for item in variants
    }
    cases = {case.id: case for case in capability_suite.cases}
    missing_seed = [case_id for case_id in seed.case_ids if case_id not in cases]
    if missing_seed:
        raise ValueError(f"narrative seed contains unknown cases: {', '.join(missing_seed)}")

    review_pairs: list[NarrativeReviewPair] = []
    mappings: list[NarrativePairMapping] = []
    variant_names = [variants[0].variant, variants[1].variant]
    for case_id in seed.case_ids:
        case = cases[case_id]
        first = outputs_by_variant[variant_names[0]].get(case_id)
        second = outputs_by_variant[variant_names[1]].get(case_id)
        if first is None or second is None:
            raise ValueError(f"capability summary is missing narrative output for {case_id}")
        if _left_is_first_variant(case_id):
            a_variant, a_output = variant_names[0], first
            b_variant, b_output = variant_names[1], second
        else:
            a_variant, a_output = variant_names[1], second
            b_variant, b_output = variant_names[0], first
        pair_id = _pair_id(case_id)

        def render(output: Any) -> NarrativeOutputForReview:
            return NarrativeOutputForReview(
                decision=str(output.decision.value if hasattr(output.decision, "value") else output.decision),
                impact_score=float(output.impact_score),
                what_changed_zh=str(output.what_changed_zh),
                why_relevant_zh=str(output.why_relevant_zh),
                recommended_actions_zh=[str(item) for item in output.recommended_actions_zh],
            )

        review_pairs.append(
            NarrativeReviewPair(
                pair_id=pair_id,
                case_id=case_id,
                case_title=case.title,
                truth_status=case.truth_status,
                A=render(a_output),
                B=render(b_output),
                dimension_preferences={dimension: None for dimension in seed.rubric_dimensions},
            )
        )
        mappings.append(
            NarrativePairMapping(
                pair_id=pair_id,
                case_id=case_id,
                A_variant=a_variant,
                B_variant=b_variant,
            )
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    review_path = output_dir / "narrative_pairs.review.json"
    mapping_path = output_dir / "narrative_pairs.mapping.json"
    calibration_eligible = summary.mode.value == "agent" and summary.comparison_valid
    review = NarrativeReviewFile(
        version=seed.version,
        capability_suite=capability_suite.suite,
        source_mode=summary.mode.value,
        source_provider=summary.provider,
        source_model=summary.model,
        calibration_eligible=calibration_eligible,
        rubric_dimensions=seed.rubric_dimensions,
        minimum_labeled_pairs=seed.minimum_labeled_pairs,
        pairs=review_pairs,
    )
    mapping = NarrativeMappingFile(
        version=seed.version,
        capability_suite=capability_suite.suite,
        mappings=mappings,
    )
    atomic_write_text(
        review_path,
        json.dumps(review.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
    )
    atomic_write_text(
        mapping_path,
        json.dumps(mapping.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
    )
    return {"review": review_path, "mapping": mapping_path}


def update_narrative_review_pair(
    path: str | Path,
    *,
    pair_id: str,
    human_preference: HumanPreference,
    dimension_preferences: dict[str, DimensionPreference],
    human_note: str = "",
) -> NarrativeReviewFile:
    """Atomically persist one complete blind human review without revealing mapping data."""

    target = Path(path)
    review = load_review_file(target)
    expected_dimensions = set(review.rubric_dimensions)
    observed_dimensions = set(dimension_preferences)
    if observed_dimensions != expected_dimensions:
        missing = sorted(expected_dimensions - observed_dimensions)
        extra = sorted(observed_dimensions - expected_dimensions)
        raise ValueError(
            f"dimension preferences must exactly match rubric; missing={missing}; extra={extra}"
        )
    pair_index = next(
        (index for index, pair in enumerate(review.pairs) if pair.pair_id == pair_id),
        None,
    )
    if pair_index is None:
        raise ValueError(f"unknown narrative pair: {pair_id}")
    pair = review.pairs[pair_index].model_copy(
        update={
            "human_preference": human_preference,
            "dimension_preferences": dict(dimension_preferences),
            "human_note": human_note.strip(),
        }
    )
    pairs = list(review.pairs)
    pairs[pair_index] = pair
    updated = review.model_copy(update={"pairs": pairs})
    atomic_write_text(
        target,
        json.dumps(updated.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
    )
    return updated


def narrative_calibration_status(review: NarrativeReviewFile) -> NarrativeCalibrationStatus:
    labeled = sum(pair.human_preference is not None for pair in review.pairs)
    dimension_counts = {
        dimension: sum(
            pair.dimension_preferences.get(dimension) is not None for pair in review.pairs
        )
        for dimension in review.rubric_dimensions
    }
    ready = review.calibration_eligible and labeled >= review.minimum_labeled_pairs
    if not review.calibration_eligible:
        blocker = (
            "The review file was generated from mock/offline or otherwise invalid comparison "
            "outputs. Human labels can test the workflow, but cannot calibrate a production judge."
        )
    elif labeled < review.minimum_labeled_pairs:
        blocker = (
            f"Need at least {review.minimum_labeled_pairs} blind human pair labels; "
            f"currently have {labeled}."
        )
    else:
        blocker = (
            "Human labels are sufficient to begin a separate judge-calibration experiment, "
            "but no LLM judge is enabled until agreement/bias checks are implemented and passed."
        )
    return NarrativeCalibrationStatus(
        version=review.version,
        pair_count=len(review.pairs),
        labeled_pairs=labeled,
        label_coverage=(labeled / len(review.pairs) if review.pairs else 0.0),
        dimension_label_counts=dimension_counts,
        calibration_eligible=review.calibration_eligible,
        minimum_labeled_pairs=review.minimum_labeled_pairs,
        ready_for_judge_calibration=ready,
        judge_calibrated=False,
        judge_enabled=False,
        blocker=blocker,
    )
