"""Project-conditioned discovery profile derived from existing project facts.

The discovery profile answers a different question from dependency monitoring: what adjacent
problem/solution spaces should this project scan for previously-unknown tools or approaches?
It is deliberately deterministic in V1 so non-AI projects never inherit a global AI/Agent radar.
"""

from __future__ import annotations

from typing import Any


def _values(profile: dict[str, Any], key: str) -> list[str]:
    raw = profile.get(key, [])
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _unique(values: list[str], limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.casefold().strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(value.strip())
        if len(result) >= limit:
            break
    return result


def _text(profile: dict[str, Any]) -> str:
    parts = [
        str(profile.get("purpose") or ""),
        str(profile.get("goal") or ""),
        *_values(profile, "tech_stack"),
        *_values(profile, "runtimes"),
        *_values(profile, "protocols"),
        *_values(profile, "providers"),
        *_values(profile, "critical_modules"),
    ]
    return " ".join(parts).casefold()


def _purpose(profile: dict[str, Any]) -> str:
    return f"{profile.get('purpose') or ''} {profile.get('goal') or ''}".casefold()


def _contains(text: str, *terms: str) -> bool:
    normalized = text.casefold()
    return any(term.casefold() in normalized for term in terms)


def derive_discovery_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Derive a bounded discovery scope without assuming the project is AI-related."""

    purpose = _purpose(profile)
    full = _text(profile)
    domain = "通用软件工程"
    problems: list[str] = []
    solutions: list[str] = []
    queries: list[str] = []
    exclusions: list[str] = []

    # Product purpose wins over incidental dependencies/modules. This prevents a game that
    # happens to contain an "agent" folder or SDK from inheriting an AI industry radar.
    if _contains(purpose, "game", "小游戏", "游戏", "casual"):
        domain = "小游戏 / 互动产品" if _contains(purpose, "mini game", "小游戏", "wechat") else "游戏 / 互动产品"
        problems.extend(["触摸与绘制交互", "游戏循环与留存", "轻量动画与资源管线"])
        solutions.extend(["轻量游戏引擎与运行时", "手势 / 绘制识别", "休闲游戏进度与反馈机制"])
        queries.extend([
            '"wechat mini game" in:name,description',
            '"gesture game" interaction in:name,description',
            '"2d game engine" typescript in:name,description',
        ])
        if not _contains(purpose, "ai", "llm", "agent"):
            exclusions.extend([
                "LLM agent",
                "AI coding agent",
                "agent framework",
                "DeepSeek Harness",
                "MCP server",
            ])
    elif _contains(purpose, "finance", "financial", "quant", "证券", "金融", "量化"):
        domain = "金融研究 / 数据分析"
        problems.extend(["市场与基本面数据获取", "可审计研究工作流", "时序与量化分析"])
        solutions.extend(["金融数据管线", "研究工作流工具", "时序 / 量化分析基础设施"])
        queries.extend([
            '"financial data" pipeline in:name,description',
            '"research workflow" finance in:name,description',
            '"time series" quant in:name,description',
        ])
    elif _contains(purpose, "data", "analytics", "etl", "olap", "数据", "分析"):
        domain = "数据工程 / 分析"
        problems.extend(["数据采集与转换", "分析查询与可复现计算", "数据质量与可观测性"])
        solutions.extend(["嵌入式分析引擎", "ETL / ELT 工具", "数据质量与 lineage 工具"])
        queries.extend([
            '"embedded analytics" database in:name,description',
            '"data pipeline" developer tool in:name,description',
            '"data quality" lineage in:name,description',
        ])
    elif _contains(purpose, "ios", "android", "mobile", "app", "客户端", "移动"):
        domain = "移动客户端"
        problems.extend(["客户端状态与交互", "端侧性能与可靠性", "系统能力接入"])
        solutions.extend(["移动端状态管理", "端侧性能工具", "原生能力集成框架"])
        queries.extend([
            '"mobile app" state management in:name,description',
            '"mobile performance" developer tool in:name,description',
            '"native integration" mobile in:name,description',
        ])
    elif _contains(purpose, "ai", "llm", "agent", "assistant"):
        domain = "AI / Agent 开发工具"
        problems.extend(["Agent 执行与工具互操作", "模型 / Provider 抽象", "运行可靠性与可观测性"])
        solutions.extend(["Agent runtime", "工具协议与沙箱", "多模型网关与 Provider 层"])
        if _contains(purpose, "channel", "messaging", "消息", "gateway"):
            problems.append("多渠道消息与网关集成")
            solutions.append("多渠道自动化与消息网关")
        queries.extend([
            '"agent runtime" tools in:name,description',
            '"tool protocol" agent in:name,description',
            '"model gateway" provider in:name,description',
        ])
    elif _contains(purpose, "api", "backend", "service", "server", "gateway", "后端", "服务"):
        domain = "Web / API 服务"
        problems.extend(["API 契约与兼容性", "服务可靠性与可观测性", "鉴权与边界治理"])
        solutions.extend(["API 框架与契约工具", "服务可观测性", "鉴权与策略组件"])
        queries.extend([
            '"api contract" developer tool in:name,description',
            '"service observability" in:name,description',
            '"api gateway" policy in:name,description',
        ])
    elif _contains(purpose, "frontend", "web app", "website", "前端", "网站"):
        domain = "Web / 前端"
        problems.extend(["前端状态与交互", "构建与开发体验", "性能与可访问性"])
        solutions.extend(["前端状态工具", "构建工具链", "Web 性能与可访问性工具"])
        queries.extend([
            '"frontend state" developer tool in:name,description',
            '"web build" developer tool in:name,description',
            '"web performance" accessibility in:name,description',
        ])
    else:
        # Generic projects still get bounded adjacent discovery from concrete modules rather than
        # a global technology-news feed. No AI terms are injected here.
        modules = [
            value
            for value in _values(profile, "critical_modules")
            if not _contains(value, "agent", "llm", "mcp", "model provider")
        ]
        stack = _values(profile, "tech_stack")
        problems.extend(modules[:3] or ["软件可靠性", "开发工作流"])
        solutions.extend([f"{value} 相邻工具" for value in (modules[:2] or stack[:2])])
        for value in (modules[:3] or stack[:3]):
            queries.append(f'"{value}" developer tool in:name,description')

    # Add a small number of real project hints only when they improve a domain-specific query;
    # do not copy the dependency list wholesale into discovery.
    if domain == "AI / Agent 开发工具" and _contains(full, "mcp", "model context protocol"):
        problems.append("工具协议与上下文互操作")
        solutions.append("MCP / 工具互操作基础设施")
    if domain in {"Web / API 服务", "Web / 前端", "通用软件工程"} and _contains(full, "authentication", "auth"):
        problems.append("认证与权限边界")

    return {
        "version": "project-discovery-v1",
        "project_domain": domain,
        "problem_spaces": _unique(problems, 6),
        "solution_categories": _unique(solutions, 6),
        "discovery_queries": _unique(queries, 3),
        "exclusions": _unique(exclusions, 6),
        "lookback_days": 30,
        "max_results_per_query": 5,
    }


def with_discovery_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Attach the deterministic discovery profile from the current project facts."""

    result = dict(profile)
    # V1 is system-derived rather than user-authored. Recompute it so a changed purpose or
    # onboarding description cannot leave a stale radar scope behind.
    result["discovery_profile"] = derive_discovery_profile(result)
    return result
