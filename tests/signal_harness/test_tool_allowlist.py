from __future__ import annotations

from signal_harness.runtime.tool_registry import (
    SIGNAL_TOOL_ALLOWLIST,
    create_signal_tool_registry,
)


def test_registry_exposes_only_signal_tools() -> None:
    registry = create_signal_tool_registry()
    names = {tool.name for tool in registry.list_tools()}

    assert names == SIGNAL_TOOL_ALLOWLIST
    assert registry.get("bash") is None
    assert registry.get("edit_file") is None
    assert registry.get("web_fetch") is None
    assert registry.get("mcp") is None


def test_registry_schemas_are_provider_safe() -> None:
    registry = create_signal_tool_registry()
    schemas = registry.to_api_schema()

    assert {schema["name"] for schema in schemas} == SIGNAL_TOOL_ALLOWLIST
    assert all("input_schema" in schema for schema in schemas)
