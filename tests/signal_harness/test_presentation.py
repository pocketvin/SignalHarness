from __future__ import annotations

from signal_harness.presentation import (
    PRESENTATION_VERSION,
    sanitize_user_facing_action,
    sanitize_user_facing_actions,
)


def test_presentation_sanitizer_extracts_substantive_chinese_action() -> None:
    assert PRESENTATION_VERSION == "presentation-v2"
    assert (
        sanitize_user_facing_action(
            "Approval required before `立即核查当前部署中 HTTPX 的实际运行版本`: "
            "立即核查当前部署中 HTTPX 的实际运行版本 is not enabled"
        )
        == "立即核查当前部署中 HTTPX 的实际运行版本"
    )


def test_presentation_sanitizer_drops_internal_ids_and_permission_boilerplate() -> None:
    actions = sanitize_user_facing_actions(
        [
            "先核对上游 release notes。",
            "Approval required before `monitor_mcp_spec_proposal_status`: "
            "monitor_mcp_spec_proposal_status is not enabled",
            "Human approval is required before execution.",
            "schema_valid=false",
            "先核对上游 release notes。",
        ]
    )

    assert actions == ["先核对上游 release notes。"]


def test_presentation_sanitizer_keeps_mixed_chinese_technical_terms() -> None:
    assert (
        sanitize_user_facing_action("在 provider adapter 中补充 HTTPX redirect 回归测试")
        == "在 provider adapter 中补充 HTTPX redirect 回归测试"
    )


def test_presentation_sanitizer_drops_short_runtime_action_labels() -> None:
    assert (
        sanitize_user_facing_action(
            "Approval required before `回归验证`: 回归验证 is not enabled"
        )
        is None
    )
