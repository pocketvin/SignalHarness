"""ProjectNarrativeAgent: human-facing Chinese summaries over guarded Scan results."""

from __future__ import annotations

import re
from typing import Any

from signal_harness.agent_integration.prompts import PROMPT_VERSION, build_agent_call
from signal_harness.agent_integration.schemas import (
    ProjectNarrativeItem,
    ProjectNarrativeOutput,
)
from signal_harness.providers.adapter import AgentCall
from signal_harness.presentation import sanitize_user_facing_actions
from signal_harness.signal.schemas import SignalAssessment, SignalEvent


class ProjectNarrativeAgent:
    """Write product copy without changing score, decision, evidence, or permissions."""

    name = "ProjectNarrativeAgent"
    prompt_version = PROMPT_VERSION
    output_model = ProjectNarrativeOutput

    def build_call(
        self,
        events: list[SignalEvent],
        assessments: list[SignalAssessment],
        *,
        project_profile: dict[str, Any],
        policy: dict[str, Any] | None = None,
        volatile_metadata: dict[str, Any] | None = None,
    ) -> AgentCall:
        assessment_by_id = {item.event_id: item for item in assessments}
        payload = {
            "project": {
                "name": project_profile.get("project_name"),
                "purpose": project_profile.get("purpose") or project_profile.get("goal"),
                "tech_stack": project_profile.get("tech_stack", []),
                "runtimes": project_profile.get("runtimes", []),
                "protocols": project_profile.get("protocols", []),
                "providers": project_profile.get("providers", []),
                "critical_modules": project_profile.get("critical_modules", []),
                "dependencies": project_profile.get("dependencies", []),
                "importance_preferences": project_profile.get("importance_preferences", []),
            },
            "changes": [
                {
                    "event": event.model_dump(mode="json"),
                    "guarded_assessment": assessment_by_id[event.event_id].model_dump(mode="json"),
                }
                for event in events
                if event.event_id in assessment_by_id
            ],
            "writing_contract": [
                "All user-facing prose must be natural Simplified Chinese.",
                "what_changed_zh must explain the actual technical event in plain language, not repeat raw markup, JSON, HTML, or a title verbatim.",
                "why_relevant_zh must connect the change to concrete project stack/modules/dependencies/preferences and explain the mechanism; do not say only '可能影响X，判定为Y，影响分Z'.",
                "recommended_actions_zh must be specific, reversible engineering next steps in Chinese. Never copy Approval required before / Human approval / is not enabled / permission ids / debug fields into product copy; keep only the substantive engineering action.",
                "report_zh is a short human project-environment brief, normally 2-5 sentences. Lead with what matters most, explain the pattern and practical implication, and avoid mechanically listing counts, categories, scores, or coverage fields.",
                "Preserve uncertainty when evidence is weak or a source fetch failed; never invent facts beyond the supplied events/evidence.",
                "Return exactly one results item for every changes[].event.event_id. Copy each event_id verbatim and do not omit an item because it is low priority, uncertain, or needs no action.",
                "This is presentation only. Never change or reinterpret the guarded score, decision, evidence authority, or permission result.",
            ],
        }
        return build_agent_call(
            agent_name=self.name,
            output_model=self.output_model,
            dynamic_payload=payload,
            input_count=len(payload["changes"]),
            project_context={
                "project_profile": project_profile,
                "policy": policy or {},
            },
            volatile_metadata=volatile_metadata,
        )

    def sanitize_output(self, output: ProjectNarrativeOutput) -> ProjectNarrativeOutput:
        """Strip runtime permission/debug boilerplate from product-facing actions."""

        return output.model_copy(
            update={
                "results": [
                    item.model_copy(
                        update={
                            "recommended_actions_zh": sanitize_user_facing_actions(
                                item.recommended_actions_zh
                            )
                        }
                    )
                    for item in output.results
                ]
            }
        )


    def fallback(
        self,
        events: list[SignalEvent],
        assessments: list[SignalAssessment],
        *,
        project_profile: dict[str, Any],
    ) -> ProjectNarrativeOutput:
        assessment_by_id = {item.event_id: item for item in assessments}
        results: list[ProjectNarrativeItem] = []
        for event in events:
            assessment = assessment_by_id.get(event.event_id)
            if assessment is None:
                continue
            modules = [item for item in assessment.affected_modules if item != "project-wide"]
            target = "、".join(modules[:3]) or "当前项目的相关工程能力"
            what = _plain_event_text(event)
            why = (
                f"这条变化和 {target} 直接相关。"
                f"需要结合项目当前使用的依赖、协议或实现方式确认是否会改变现有行为；"
                "在证据没有进一步确认前，不把它当成已经发生在本项目里的故障。"
            )
            actions = [
                "先核对上游原始说明和变更范围，确认与当前项目使用方式是否真正重叠。",
                f"如果存在重叠，在 {target} 做一组针对性的兼容性或回归验证，再决定是否需要改代码。",
            ]
            results.append(
                ProjectNarrativeItem(
                    event_id=event.event_id,
                    what_changed_zh=what,
                    why_relevant_zh=why,
                    recommended_actions_zh=actions,
                )
            )
        project_name = str(project_profile.get("project_name") or "当前项目")
        if results:
            highlights = "；".join(item.what_changed_zh for item in results[:2])
            report = (
                f"这次对 {project_name} 真正值得看的不是信息数量，而是这些变化是否会碰到现有工程边界。"
                f"目前较值得继续核实的是：{highlights}。"
                "建议先确认上游事实与项目实际用法的交集，再把验证结果转成具体的兼容性测试或改动任务。"
            )
        else:
            report = f"这次没有形成足够明确、值得立即展开的 {project_name} 项目影响结论。"
        return ProjectNarrativeOutput(report_zh=report, results=results)


def _plain_event_text(event: SignalEvent) -> str:
    raw = event.content or event.title
    text = re.sub(r"```[\s\S]*?```", " ", raw)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[`*_>#{}\[\]]", " ", text)
    text = " ".join(text.split()).strip()
    if not text or len(text) < 12:
        text = event.title.strip()
    if len(text) > 320:
        text = text[:317].rstrip() + "…"
    return text
