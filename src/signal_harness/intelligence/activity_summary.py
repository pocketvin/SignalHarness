"""Deterministic presentation summary for project-owned activity.

This is intentionally not model-generated intelligence. Project activity is contextual evidence
about what the monitored project itself changed; grouping it for the UI must not create a second
semantic truth source or let project activity support EnvironmentDirections.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

_CONVENTIONAL = re.compile(
    r"^(?P<type>feat|fix|refactor|test|tests|docs|perf|chore|build|ci|style|revert)"
    r"(?:\((?P<scope>[^)]+)\))?!?:\s*(?P<body>.+)$",
    re.IGNORECASE,
)
_TRAILING_PR = re.compile(r"\s*\(#\d+\)\s*$")

_SCOPE_LABELS = {
    "ui": "前端 / UI",
    "web": "前端 / UI",
    "frontend": "前端 / UI",
    "control-ui": "前端 / UI",
    "tui": "前端 / UI",
    "agent": "Agent / 执行",
    "agents": "Agent / 执行",
    "subagent": "Agent / 执行",
    "subagents": "Agent / 执行",
    "orchestration": "Agent / 执行",
    "provider": "模型 / Provider",
    "providers": "模型 / Provider",
    "model": "模型 / Provider",
    "models": "模型 / Provider",
    "openai": "模型 / Provider",
    "anthropic": "模型 / Provider",
    "gemini": "模型 / Provider",
    "mistral": "模型 / Provider",
    "sdk": "SDK / API",
    "mcp": "SDK / API",
    "api": "SDK / API",
    "gateway": "SDK / API",
    "test": "测试 / 质量",
    "tests": "测试 / 质量",
    "testing": "测试 / 质量",
    "qa": "测试 / 质量",
    "status": "测试 / 质量",
    "ci": "测试 / 质量",
    "android": "客户端",
    "ios": "客户端",
    "macos": "客户端",
    "windows": "客户端",
    "mobile": "客户端",
    "security": "安全 / 鉴权",
    "auth": "安全 / 鉴权",
    "secrets": "安全 / 鉴权",
    "docs": "文档",
    "documentation": "文档",
    "runtime": "运行时 / 状态",
    "session": "运行时 / 状态",
    "memory": "运行时 / 状态",
    "cron": "运行时 / 状态",
    "plugin": "插件 / 扩展",
    "plugins": "插件 / 扩展",
    "extensions": "插件 / 扩展",
    "update": "工程 / 发布",
    "release": "工程 / 发布",
    "build": "工程 / 发布",
    "scripts": "工程 / 发布",
    "doctor": "工程 / 发布",
    "cli": "工程 / 发布",
    "config": "工程 / 发布",
    "tasks": "Agent / 执行",
    "workers": "Agent / 执行",
    "reply": "Agent / 执行",
    "codex": "Agent / 执行",
    "sessions": "状态 / 存储",
    "state": "状态 / 存储",
    "sqlite": "状态 / 存储",
    "logbook": "状态 / 存储",
    "channel": "网关 / 渠道",
    "channels": "网关 / 渠道",
    "matrix": "网关 / 渠道",
    "slack": "网关 / 渠道",
    "discord": "网关 / 渠道",
    "telegram": "网关 / 渠道",
    "whatsapp": "网关 / 渠道",
    "bonjour": "网关 / 渠道",
    "e2e": "测试 / 质量",
    "i18n": "国际化 / 文档",
    "docs-i18n": "国际化 / 文档",
}
_WORK_AREA_LABELS = set(_SCOPE_LABELS.values())

_TYPE_LABELS = {
    "feat": "功能开发",
    "fix": "Bug 修复",
    "refactor": "重构",
    "test": "测试 / 质量",
    "tests": "测试 / 质量",
    "docs": "文档",
    "perf": "性能",
    "chore": "工程维护",
    "build": "工程维护",
    "ci": "测试 / 质量",
    "style": "工程维护",
    "revert": "回退 / 修正",
}
_KIND_LABELS = {
    "github_commit": "代码提交",
    "github_pull_request": "合并变更",
    "local_git_commit": "代码提交",
    "github_release": "版本发布",
}


def _activity_bucket(title: str, kind: str) -> tuple[str, str, str]:
    """Return (bucket key, Chinese label, concise exact activity text)."""

    raw = title.strip()
    match = _CONVENTIONAL.match(raw)
    if match:
        commit_type = match.group("type").casefold()
        scope = (match.group("scope") or "").strip().casefold()
        body = _TRAILING_PR.sub("", match.group("body").strip())
        label = _SCOPE_LABELS.get(scope) or _TYPE_LABELS.get(commit_type, "项目改动")
        key = label.casefold()
        return key, label, body or raw
    label = _KIND_LABELS.get(kind, "其他项目改动")
    return label.casefold(), label, _TRAILING_PR.sub("", raw)


def summarize_project_activity(
    items: list[dict[str, Any]], *, max_groups: int = 4, samples_per_group: int = 3
) -> dict[str, Any]:
    """Summarize canonical project-activity Changes without model calls."""

    if max_groups < 1 or samples_per_group < 1:
        raise ValueError("activity summary bounds must be positive")

    groups: dict[str, list[tuple[dict[str, Any], str, str]]] = defaultdict(list)
    type_counts: Counter[str] = Counter()
    for item in items:
        if item.get("corpus_role") != "project_activity":
            continue
        title = str(item.get("title") or item.get("summary") or "项目改动").strip()
        kind = str(item.get("kind") or "unknown")
        key, label, concise = _activity_bucket(title, kind)
        groups[key].append((item, label, concise))
        match = _CONVENTIONAL.match(title)
        type_counts[(match.group("type").casefold() if match else kind)] += 1

    ranked = sorted(
        groups.items(),
        key=lambda pair: (
            0 if pair[1][0][1] in _WORK_AREA_LABELS else 1,
            -len(pair[1]),
            pair[1][0][1].casefold(),
        ),
    )
    result_groups: list[dict[str, Any]] = []
    shown = 0
    for key, rows in ranked[:max_groups]:
        del key
        rows.sort(
            key=lambda row: (
                str(row[0].get("published_at") or ""),
                str(row[0].get("change_id") or ""),
            ),
            reverse=True,
        )
        samples = []
        seen_text: set[str] = set()
        for item, _label, concise in rows:
            normalized = concise.casefold()
            if normalized in seen_text:
                continue
            seen_text.add(normalized)
            samples.append(
                {
                    "change_id": str(item.get("change_id") or ""),
                    "text": concise,
                    "published_at": item.get("published_at"),
                    "kind": str(item.get("kind") or "unknown"),
                }
            )
            if len(samples) >= samples_per_group:
                break
        shown += len(rows)
        result_groups.append(
            {
                "label": rows[0][1],
                "count": len(rows),
                "samples": samples,
            }
        )

    total_count = sum(len(rows) for rows in groups.values())
    return {
        "total_count": total_count,
        "groups": result_groups,
        "other_count": max(0, total_count - shown),
        "type_counts": dict(type_counts.most_common()),
    }
