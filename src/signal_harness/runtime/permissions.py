"""Business-level permission policy for SignalHarness operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SignalPermissionDecision:
    allowed: bool
    requires_confirmation: bool = False
    reason: str = ""


class SignalPermissionGuard:
    """Evaluate product actions separately from generic file/shell permissions."""

    DEFAULT_AUTO_ALLOW = frozenset(
        {
            "read_github_release",
            "read_github_issue",
            "read_rss",
            "read_mock_web_change",
            "read_config",
            "write_local_report",
            "write_task_trace",
            "save_feedback",
            "save_policy_proposal",
        }
    )
    DEFAULT_REQUIRE_CONFIRMATION = frozenset(
        {
            "modify_signal_policy",
            "add_watchlist_source",
            "remove_watchlist_source",
            "create_github_issue",
            "send_team_notification",
            "modify_project_profile",
        }
    )

    def __init__(self, policy: dict[str, Any] | None = None) -> None:
        configured = (policy or {}).get("permission_policy", {})
        self.auto_allow = frozenset(
            configured.get("auto_allow", self.DEFAULT_AUTO_ALLOW)
        ) | {"save_feedback", "save_policy_proposal"}
        self.require_confirmation = frozenset(
            configured.get(
                "require_confirmation",
                self.DEFAULT_REQUIRE_CONFIRMATION,
            )
        )

    def evaluate(self, action: str) -> SignalPermissionDecision:
        if action in self.auto_allow:
            return SignalPermissionDecision(True, reason=f"{action} is low risk")
        if action in self.require_confirmation:
            return SignalPermissionDecision(
                False,
                requires_confirmation=True,
                reason=f"{action} requires explicit user approval",
            )
        return SignalPermissionDecision(False, reason=f"{action} is not enabled")

    def require(self, action: str, *, confirmed: bool = False) -> None:
        decision = self.evaluate(action)
        if decision.allowed:
            return
        if decision.requires_confirmation and confirmed:
            return
        raise PermissionError(decision.reason)
