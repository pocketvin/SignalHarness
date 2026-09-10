"""Public summaries of structured Agent outputs for live Trace presentation.

These summaries deliberately expose only fields the Agent already returned in its
validated output schema. They are not hidden chain-of-thought and never include the
raw provider response or prompt text.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from signal_harness.presentation import sanitize_user_facing_actions

TRACE_REASONING_VERSION = "structured-reasoning-v1"
_DISCLOSURE = "structured_model_output_summary_not_hidden_chain_of_thought"
_MAX_SUMMARY_CHARS = 1600
_MAX_ITEM_SUMMARY_CHARS = 1200
_MAX_ITEMS = 12
_MAX_LIST_ITEMS = 8


def public_reasoning_metadata(output: BaseModel) -> dict[str, Any]:
    """Build bounded, UI-safe reasoning metadata from one validated Agent output."""

    payload = output.model_dump(mode="json")
    schema = output.__class__.__name__
    summary = ""
    items: list[dict[str, Any]] = []

    if schema == "SupervisorOutput":
        summary = _text(payload.get("batch_summary"))
        for route in _list_of_dicts(payload.get("routes")):
            items.append(
                _item(
                    route,
                    summary=_text(route.get("routing_reason") or route.get("noise_reason")),
                    fields={
                        "category": route.get("category"),
                        "analyze": route.get("analyze"),
                        "required_agents": _short_list(route.get("required_agents")),
                    },
                )
            )
    elif schema == "EvidenceToolPlan":
        summary = _text(payload.get("planning_summary"))
        for request in _list_of_dicts(payload.get("tool_requests")):
            items.append(
                {
                    "title": _text(request.get("tool_name")) or "tool",
                    "summary": _clip(_text(request.get("reason")), _MAX_ITEM_SUMMARY_CHARS),
                }
            )
    elif schema == "ContextEvidenceOutput":
        for result in _list_of_dicts(payload.get("results")):
            uncertainty = _text(result.get("uncertainty"))
            item = _item(
                result,
                summary=_text(result.get("context_summary")),
                fields={
                    "confidence": result.get("confidence"),
                    "source_quality": result.get("source_quality"),
                    "uncertainty": uncertainty or None,
                    "unsupported_claims": _short_list(result.get("unsupported_claims")),
                },
            )
            items.append(item)
        summary = _summarize_items(items, "Evidence context returned for the analyzed events.")
    elif schema == "ImpactOutput":
        items = [_impact_item(result) for result in _list_of_dicts(payload.get("results"))]
        summary = _summarize_items(items, "Project-impact reasoning returned for the analyzed events.")
    elif schema == "ImpactActionOutput":
        for result in _list_of_dicts(payload.get("results")):
            raw_impact = result.get("impact")
            raw_action = result.get("action")
            impact: dict[str, Any] = raw_impact if isinstance(raw_impact, dict) else {}
            action: dict[str, Any] = raw_action if isinstance(raw_action, dict) else {}
            event_id = _text(
                result.get("event_id") or impact.get("event_id") or action.get("event_id")
            )
            fields: dict[str, Any] = {
                "risk_level": impact.get("risk_level"),
                "semantic_relevance": impact.get("semantic_relevance"),
                "affected_modules": _short_list(impact.get("affected_modules")),
                "conflicting_evidence": _short_list(impact.get("conflicting_evidence")),
            }
            safe_actions = sanitize_user_facing_actions(action.get("action_items") or [])
            if safe_actions:
                fields["recommended_actions"] = safe_actions[:_MAX_LIST_ITEMS]
            items.append(
                {
                    "event_id": event_id,
                    "summary": _clip(_text(impact.get("impact_reason")), _MAX_ITEM_SUMMARY_CHARS),
                    **_without_empty(fields),
                }
            )
        summary = _summarize_items(items, "Impact and action reasoning returned for the analyzed events.")
    elif schema == "ActionOutput":
        for result in _list_of_dicts(payload.get("results")):
            safe_actions = sanitize_user_facing_actions(result.get("action_items") or [])
            items.append(
                _item(
                    result,
                    summary=("；".join(safe_actions) if safe_actions else "Structured action proposal returned."),
                    fields={
                        "approval_required": result.get("approval_required"),
                        "requested_action_count": len(result.get("requested_actions") or []),
                    },
                )
            )
        summary = _summarize_items(items, "Action proposals returned for the analyzed events.")
    elif schema == "ProjectNarrativeOutput":
        summary = _text(payload.get("report_zh"))
        for result in _list_of_dicts(payload.get("results")):
            items.append(
                _item(
                    result,
                    summary=_text(result.get("why_relevant_zh") or result.get("what_changed_zh")),
                    fields={
                        "what_changed_zh": _text(result.get("what_changed_zh")) or None,
                        "recommended_actions_zh": sanitize_user_facing_actions(
                            result.get("recommended_actions_zh") or []
                        )[:_MAX_LIST_ITEMS],
                    },
                )
            )
    elif schema == "VerificationOutput":
        for result in _list_of_dicts(payload.get("results")):
            items.append(
                _item(
                    result,
                    summary=_text(result.get("notes")) or "Verification result returned.",
                    fields={
                        "supported": result.get("supported"),
                        "confidence_cap": result.get("confidence_cap"),
                        "impact_overstated": result.get("impact_overstated"),
                        "action_overstated": result.get("action_overstated"),
                    },
                )
            )
        summary = _summarize_items(items, "Verification results returned.")
    elif schema == "LearningPolicyOutput":
        summary = _text(payload.get("learning_summary"))
    else:
        summary = "Validated structured Agent output returned."

    bounded_items = [item for item in items[:_MAX_ITEMS] if item.get("summary") or item.get("event_id")]
    return {
        "reasoning_version": TRACE_REASONING_VERSION,
        "reasoning_disclosure": _DISCLOSURE,
        "reasoning_state": "available",
        "reasoning_summary": _clip(summary or "Validated structured Agent output returned.", _MAX_SUMMARY_CHARS),
        "reasoning_items": bounded_items,
    }


def waiting_reasoning_metadata() -> dict[str, Any]:
    """Metadata emitted with the running trace before the provider responds."""

    return {
        "reasoning_version": TRACE_REASONING_VERSION,
        "reasoning_disclosure": _DISCLOSURE,
        "reasoning_state": "waiting_for_model",
    }


def _impact_item(result: dict[str, Any]) -> dict[str, Any]:
    return _item(
        result,
        summary=_text(result.get("impact_reason")),
        fields={
            "risk_level": result.get("risk_level"),
            "semantic_relevance": result.get("semantic_relevance"),
            "affected_modules": _short_list(result.get("affected_modules")),
            "conflicting_evidence": _short_list(result.get("conflicting_evidence")),
        },
    )


def _item(
    payload: dict[str, Any],
    *,
    summary: str,
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event_id": _text(payload.get("event_id")),
        "summary": _clip(summary, _MAX_ITEM_SUMMARY_CHARS),
        **_without_empty(fields or {}),
    }


def _summarize_items(items: list[dict[str, Any]], fallback: str) -> str:
    first = next((_text(item.get("summary")) for item in items if _text(item.get("summary"))), "")
    if not first:
        return fallback
    if len(items) <= 1:
        return first
    return f"{len(items)} 个事件完成结构化判断。首条摘要：{first}"


def _short_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clip(_text(item), 180) for item in value[:_MAX_LIST_ITEMS] if _text(item)]


def _list_of_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _without_empty(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in values.items()
        if value is not None and value != "" and value != []
    }


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _clip(value: str, limit: int) -> str:
    value = _text(value)
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"
