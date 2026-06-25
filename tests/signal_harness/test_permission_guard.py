from __future__ import annotations

import pytest

from signal_harness.runtime.permissions import SignalPermissionGuard


def test_low_risk_read_is_automatically_allowed() -> None:
    decision = SignalPermissionGuard().evaluate("read_github_release")

    assert decision.allowed is True
    assert decision.requires_confirmation is False


def test_policy_modification_requires_confirmation() -> None:
    guard = SignalPermissionGuard()

    decision = guard.evaluate("modify_signal_policy")

    assert decision.allowed is False
    assert decision.requires_confirmation is True
    with pytest.raises(PermissionError):
        guard.require("modify_signal_policy")
    guard.require("modify_signal_policy", confirmed=True)


def test_create_issue_requires_confirmation() -> None:
    decision = SignalPermissionGuard().evaluate("create_github_issue")

    assert decision.allowed is False
    assert decision.requires_confirmation is True
