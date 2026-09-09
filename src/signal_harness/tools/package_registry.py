"""PyPI package-registry release collection."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Literal

import httpx
from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import Version
from pydantic import BaseModel, Field, field_validator

from signal_harness.runtime.tools_base import BaseTool, ToolExecutionContext, ToolResult

PYPI_SIMPLE_ACCEPT = "application/vnd.pypi.simple.v1+json"
MAX_PYPI_RELEASES = 500
MAX_PYPI_ATTEMPTS = 3
SUPPORTED_SIMPLE_API_MAJOR = 1
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class PackageRegistryInput(BaseModel):
    action: Literal["fetch_pypi_releases"] = "fetch_pypi_releases"
    package: str = Field(min_length=1, max_length=200)
    since: datetime | None = None

    @field_validator("package")
    @classmethod
    def _validate_package(cls, value: str) -> str:
        normalized = canonicalize_name(value.strip())
        if not normalized or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in normalized):
            raise ValueError("package must be a valid PyPI project name")
        return normalized


class PackageRegistryTool(BaseTool):
    """Read package release metadata from PyPI's JSON Simple/Index API."""

    name = "package_registry"
    description = "Fetch versioned PyPI release metadata from the official JSON Index API."
    input_model = PackageRegistryInput

    def is_read_only(self, arguments: PackageRegistryInput) -> bool:
        del arguments
        return True

    async def execute(
        self,
        arguments: PackageRegistryInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        try:
            payload, headers = await _fetch_pypi_simple(arguments.package)
            releases, metadata = _release_rows(
                arguments.package,
                payload,
                since=arguments.since,
            )
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return ToolResult(output=f"PyPI request failed: {exc}", is_error=True)
        metadata["etag"] = headers.get("etag")
        metadata["last_serial"] = _serial(payload, headers)
        return ToolResult(
            output=json.dumps(releases, ensure_ascii=False),
            metadata=metadata,
        )

async def _fetch_pypi_simple(package: str) -> tuple[dict[str, Any], dict[str, str]]:
    endpoint = f"https://pypi.org/simple/{canonicalize_name(package)}/"
    headers = {
        "Accept": PYPI_SIMPLE_ACCEPT,
        "User-Agent": "SignalHarness/0.1 (+https://github.com/pocketvin/SignalHarness)",
    }
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for attempt in range(MAX_PYPI_ATTEMPTS):
            response = await client.get(endpoint, headers=headers)
            if response.status_code not in _RETRYABLE_STATUS:
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("PyPI returned a non-object JSON payload")
                _validate_api_version(payload)
                return payload, {key.lower(): value for key, value in response.headers.items()}
            if attempt + 1 >= MAX_PYPI_ATTEMPTS:
                response.raise_for_status()
            await asyncio.sleep(_retry_delay(response, attempt))
    raise RuntimeError("unreachable PyPI retry state")


def _api_version(payload: dict[str, Any]) -> str:
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return "1.0"
    raw = str(meta.get("api-version") or "1.0").strip()
    return raw or "1.0"


def _validate_api_version(payload: dict[str, Any]) -> None:
    raw = _api_version(payload)
    major_text = raw.split(".", 1)[0]
    try:
        major = int(major_text)
    except ValueError as exc:
        raise ValueError(f"PyPI returned an invalid Simple API version: {raw}") from exc
    if major > SUPPORTED_SIMPLE_API_MAJOR:
        raise ValueError(f"unsupported PyPI Simple API major version: {raw}")


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    raw = response.headers.get("Retry-After", "").strip()
    if raw:
        try:
            return float(min(2.0, max(0.0, float(raw))))
        except ValueError:
            pass
    delay = 0.2 * float(2**attempt)
    return 1.0 if delay > 1.0 else delay


def _serial(payload: dict[str, Any], headers: dict[str, str]) -> int | None:
    meta = payload.get("meta")
    value = meta.get("_last-serial") if isinstance(meta, dict) else None
    value = value if value is not None else headers.get("x-pypi-last-serial")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None

def _release_rows(
    package: str,
    payload: dict[str, Any],
    *,
    since: datetime | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    files = payload.get("files")
    if not isinstance(files, list):
        raise ValueError("PyPI JSON Index response is missing files")
    grouped: dict[Version, list[dict[str, Any]]] = defaultdict(list)
    skipped = 0
    for item in files:
        if not isinstance(item, dict):
            skipped += 1
            continue
        parsed = _distribution_version(package, str(item.get("filename") or ""))
        if parsed is None:
            skipped += 1
            continue
        grouped[parsed].append(item)

    ordered: list[tuple[Version, datetime, list[dict[str, Any]]]] = []
    for version, artifacts in grouped.items():
        uploads = [_upload_time(item.get("upload-time")) for item in artifacts]
        valid_uploads = [value for value in uploads if value is not None]
        if not valid_uploads:
            skipped += len(artifacts)
            continue
        ordered.append((version, min(valid_uploads), artifacts))
    ordered.sort(key=lambda row: (row[1], row[0]), reverse=True)

    versions = sorted(grouped)
    previous: dict[Version, Version] = {
        current: older for older, current in zip(versions, versions[1:])
    }
    eligible = [row for row in ordered if since is None or _at_or_after(row[1], since)]
    history_limited = len(eligible) > MAX_PYPI_RELEASES
    eligible = eligible[:MAX_PYPI_RELEASES]
    rows = [
        _release_payload(package, version, upload, artifacts, previous.get(version))
        for version, upload, artifacts in eligible
    ]
    diagnostics: list[str] = []
    if history_limited:
        diagnostics.append(f"release history capped at {MAX_PYPI_RELEASES} versions")
    if skipped:
        diagnostics.append(f"ignored {skipped} unsupported or timestamp-less distribution file(s)")
    return rows, {
        "pages_fetched": 1,
        "coverage_status": "partial" if history_limited else "complete",
        "history_limited": history_limited,
        "diagnostics": diagnostics,
        "item_count": len(rows),
        "registry": "pypi",
        "package": canonicalize_name(package),
        "api_version": _api_version(payload),
    }

def _distribution_version(package: str, filename: str) -> Version | None:
    if not filename:
        return None
    try:
        if filename.endswith(".whl"):
            name, version, _, _ = parse_wheel_filename(filename)
        else:
            name, version = parse_sdist_filename(filename)
    except (InvalidWheelFilename, InvalidSdistFilename):
        return None
    if canonicalize_name(name) != canonicalize_name(package):
        return None
    return version


def _upload_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _at_or_after(value: datetime, since: datetime) -> bool:
    lower = since if since.tzinfo is not None else since.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc) >= lower.astimezone(timezone.utc)


def _release_payload(
    package: str,
    version: Version,
    upload_time: datetime,
    artifacts: list[dict[str, Any]],
    previous: Version | None,
) -> dict[str, Any]:
    yanked_values = [item.get("yanked", False) for item in artifacts]
    yanked_count = sum(value is True or isinstance(value, str) for value in yanked_values)
    reasons = [str(value) for value in yanked_values if isinstance(value, str) and value.strip()]
    all_yanked = bool(artifacts) and yanked_count == len(artifacts)
    version_text = str(version)
    return {
        "source_type": "package_registry",
        "source_name": canonicalize_name(package),
        "package_name": canonicalize_name(package),
        "registry": "pypi",
        "title": f"{canonicalize_name(package)} {version_text}",
        "content": (
            f"PyPI release {version_text} for {canonicalize_name(package)}; "
            f"{len(artifacts)} distribution file(s), {yanked_count} yanked."
        ),
        "url": f"https://pypi.org/project/{canonicalize_name(package)}/{version_text}/",
        "published_at": upload_time.isoformat(),
        "upload_time": upload_time.isoformat(),
        "change_kind": "released",
        "current_version": version_text,
        "previous_version": str(previous) if previous is not None else None,
        "file_count": len(artifacts),
        "yanked": all_yanked,
        "yanked_file_count": yanked_count,
        "yanked_reason": "; ".join(dict.fromkeys(reasons)) or None,
        "official": True,
        "source_authority": "official",
    }
