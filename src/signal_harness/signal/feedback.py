"""Readable JSON feedback memory and policy calibration proposals."""

from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from openharness.utils.fs import atomic_write_text
from signal_harness.signal.schemas import FeedbackLabel, FeedbackRecord, PolicyUpdateProposal


def load_feedback_history(path: str | Path) -> list[FeedbackRecord]:
    """Load feedback records from JSON, returning an empty history when absent."""

    target = Path(path)
    if not target.exists():
        return []
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("feedback memory must contain a JSON list")
    return [FeedbackRecord.model_validate(item) for item in payload]


def save_feedback(path: str | Path, record: FeedbackRecord) -> Path:
    """Append feedback atomically without rewriting policy."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    history = load_feedback_history(target)
    history.append(record)
    payload = [item.model_dump(mode="json") for item in history]
    atomic_write_text(target, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return target


def create_feedback_record(
    event_id: str,
    label: FeedbackLabel | str,
    note: str = "",
) -> FeedbackRecord:
    """Create a timestamped feedback record."""

    return FeedbackRecord(
        event_id=event_id,
        feedback=FeedbackLabel(label),
        note=note,
        created_at=datetime.now(timezone.utc),
    )


def _record_note_keywords(
    history: Iterable[FeedbackRecord],
    labels: set[FeedbackLabel],
) -> list[str]:
    words: Counter[str] = Counter()
    stop = {
        "this",
        "that",
        "with",
        "from",
        "signal",
        "important",
        "useful",
        "related",
        "generic",
        "positive",
        "false",
    }
    for record in history:
        if record.feedback not in labels:
            continue
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", record.note.lower()):
            if word not in stop:
                words[word] += 1
    return [word for word, _ in words.most_common(5)]


def generate_policy_proposal(
    history: list[FeedbackRecord],
    policy: dict[str, Any],
) -> PolicyUpdateProposal:
    """Generate a review-only calibration proposal from recent feedback."""

    new_policy = deepcopy(policy)
    counts = Counter(record.feedback for record in history)
    thresholds = dict(new_policy.get("thresholds", {}))
    positive_keywords = _record_note_keywords(
        history,
        {FeedbackLabel.USEFUL, FeedbackLabel.MISSED_SIGNAL},
    )
    negative_keywords = _record_note_keywords(
        history,
        {FeedbackLabel.FALSE_POSITIVE, FeedbackLabel.TOO_GENERIC},
    )
    changed_keywords = list(dict.fromkeys([*positive_keywords, *negative_keywords]))
    reasons: list[str] = []

    if counts[FeedbackLabel.FALSE_POSITIVE] > counts[FeedbackLabel.MISSED_SIGNAL]:
        thresholds["alert"] = min(95, int(thresholds.get("alert", 75)) + 3)
        thresholds["action_required"] = min(
            98, int(thresholds.get("action_required", 85)) + 2
        )
        reasons.append("false positives exceed missed signals")
    elif counts[FeedbackLabel.MISSED_SIGNAL]:
        thresholds["save"] = max(20, int(thresholds.get("save", 45)) - 3)
        thresholds["alert"] = max(
            thresholds["save"], int(thresholds.get("alert", 75)) - 2
        )
        reasons.append("missed signals require more sensitive thresholds")

    if positive_keywords:
        suggestions = list(new_policy.get("suggested_focus_keywords", []))
        new_policy["suggested_focus_keywords"] = list(
            dict.fromkeys([*suggestions, *positive_keywords])
        )
        keyword_weights = {
            str(key): float(value)
            for key, value in dict(new_policy.get("keyword_weights", {})).items()
        }
        for keyword in positive_keywords:
            keyword_weights[keyword] = round(
                min(2.0, max(1.1, keyword_weights.get(keyword, 1.0) + 0.1)),
                2,
            )
        new_policy["keyword_weights"] = keyword_weights
        reasons.append("useful feedback notes contain recurring project terms")

    if negative_keywords:
        ignore_patterns = [
            str(value).strip().lower()
            for value in new_policy.get("ignore_patterns", [])
            if str(value).strip()
        ]
        new_policy["ignore_patterns"] = list(
            dict.fromkeys([*ignore_patterns, *negative_keywords])
        )
        keyword_weights = {
            str(key): float(value)
            for key, value in dict(new_policy.get("keyword_weights", {})).items()
        }
        for keyword in negative_keywords:
            if keyword in keyword_weights:
                keyword_weights[keyword] = round(
                    max(0.5, keyword_weights[keyword] - 0.2),
                    2,
                )
        new_policy["keyword_weights"] = keyword_weights
        reasons.append("negative feedback adds ignore patterns or reduces keyword weight")

    new_policy["thresholds"] = thresholds
    if not reasons:
        reasons.append("feedback volume is limited; preserve current thresholds")

    return PolicyUpdateProposal(
        proposal_id=f"proposal-{uuid4().hex[:12]}",
        reason="; ".join(reasons),
        old_policy=policy,
        new_policy=new_policy,
        changed_keywords=changed_keywords,
        changed_sources=[],
        expected_effect=(
            "Adjust future ranking while preserving all historical assessments and requiring "
            "explicit approval before policy replacement."
        ),
        requires_approval=True,
    )


def save_policy_proposal(path: str | Path, proposal: PolicyUpdateProposal) -> Path:
    """Persist the latest proposal as reviewable JSON."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        target,
        json.dumps(proposal.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
    )
    return target
