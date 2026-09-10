"""OSV vulnerability matching against concrete resolved project dependency versions."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, field_validator

from signal_harness.runtime.tools_base import BaseTool, ToolExecutionContext, ToolResult

OSV_QUERY_URL = "https://api.osv.dev/v1/query"
MAX_OSV_DEPENDENCIES = 200
MAX_OSV_PAGES_PER_DEPENDENCY = 5
MAX_OSV_CONCURRENCY = 8


class SecurityDependency(BaseModel):
    name: str
    ecosystem: Literal["PyPI", "npm"]
    version: str

    @field_validator("name", "version")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("dependency name/version must not be blank")
        return normalized


class SecurityOsvInput(BaseModel):
    action: Literal["query_dependencies"] = "query_dependencies"
    dependencies: list[SecurityDependency] = Field(max_length=MAX_OSV_DEPENDENCIES)


class SecurityOsvTool(BaseTool):
    """Query OSV using only exact resolved versions supplied by project state."""

    name = "security_osv"
    description = "Query OSV for vulnerabilities affecting exact resolved PyPI/npm dependency versions."
    input_model = SecurityOsvInput

    def is_read_only(self, arguments: SecurityOsvInput) -> bool:
        return True

    async def execute(
        self,
        arguments: SecurityOsvInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        if not arguments.dependencies:
            return ToolResult(
                output="[]",
                metadata={
                    "coverage_status": "complete",
                    "history_limited": False,
                    "pages_fetched": 0,
                    "item_count": 0,
                    "diagnostics": ["no supported resolved dependencies to query"],
                },
            )

        semaphore = asyncio.Semaphore(MAX_OSV_CONCURRENCY)
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                results = await asyncio.gather(
                    *(
                        _query_dependency(client, dependency, semaphore)
                        for dependency in arguments.dependencies
                    )
                )
        except (httpx.HTTPError, ValueError) as exc:
            return ToolResult(output=f"OSV request failed: {exc}", is_error=True)

        successful = 0
        pages_fetched = 0
        history_limited = False
        diagnostics: list[str] = []
        advisories: dict[str, dict[str, Any]] = {}
        for dependency, result in zip(arguments.dependencies, results, strict=True):
            pages_fetched += result["pages_fetched"]
            history_limited = history_limited or result["history_limited"]
            error = result.get("error")
            if error:
                diagnostics.append(f"{dependency.ecosystem}:{dependency.name}@{dependency.version}: {error}")
                continue
            successful += 1
            for raw in result["vulns"]:
                advisory_id = str(raw.get("id") or "").strip()
                if not advisory_id:
                    continue
                merged = advisories.setdefault(advisory_id, dict(raw))
                matches = merged.setdefault("matched_dependencies", [])
                match = {
                    "name": dependency.name,
                    "ecosystem": dependency.ecosystem,
                    "version": dependency.version,
                }
                if match not in matches:
                    matches.append(match)
                merged.setdefault("matched_package", dependency.name)
                merged.setdefault("matched_ecosystem", dependency.ecosystem)
                merged.setdefault("matched_version", dependency.version)
                merged.setdefault("osv_url", f"https://osv.dev/vulnerability/{advisory_id}")

        if successful == 0 and diagnostics:
            return ToolResult(output="OSV request failed for all dependencies: " + "; ".join(diagnostics), is_error=True)
        partial = successful != len(arguments.dependencies) or history_limited
        if history_limited:
            diagnostics.append(
                f"OSV pagination capped at {MAX_OSV_PAGES_PER_DEPENDENCY} pages for at least one dependency"
            )
        rows = list(advisories.values())
        rows.sort(key=lambda item: str(item.get("modified") or item.get("published") or ""), reverse=True)
        return ToolResult(
            output=json.dumps(rows, ensure_ascii=False),
            metadata={
                "coverage_status": "partial" if partial else "complete",
                "history_limited": history_limited,
                "pages_fetched": pages_fetched,
                "item_count": len(rows),
                "queried_dependencies": len(arguments.dependencies),
                "successful_dependencies": successful,
                "diagnostics": diagnostics,
            },
        )


async def _query_dependency(
    client: httpx.AsyncClient,
    dependency: SecurityDependency,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    base_query: dict[str, Any] = {
        "package": {"name": dependency.name, "ecosystem": dependency.ecosystem},
        "version": dependency.version,
    }
    vulns: list[dict[str, Any]] = []
    page_token = ""
    pages = 0
    history_limited = False
    try:
        while pages < MAX_OSV_PAGES_PER_DEPENDENCY:
            payload = dict(base_query)
            if page_token:
                payload["page_token"] = page_token
            async with semaphore:
                response = await client.post(OSV_QUERY_URL, json=payload)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("OSV returned a non-object payload")
            page_vulns = data.get("vulns", [])
            if not isinstance(page_vulns, list) or not all(isinstance(item, dict) for item in page_vulns):
                raise ValueError("OSV returned an invalid vulnerability list")
            vulns.extend(page_vulns)
            pages += 1
            page_token = str(data.get("next_page_token") or "")
            if not page_token:
                break
        if page_token:
            history_limited = True
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "vulns": vulns,
            "pages_fetched": pages,
            "history_limited": history_limited,
            "error": str(exc),
        }
    return {
        "vulns": vulns,
        "pages_fetched": pages,
        "history_limited": history_limited,
        "error": "",
    }
