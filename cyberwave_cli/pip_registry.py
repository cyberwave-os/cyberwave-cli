"""Pip release-channel helpers and PEP 503 simple-index parsing.

Selects pip package versions based on release channels (stable / dev / staging)
and fetches available versions from Buildkite-hosted Python registries.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from pathlib import Path

from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename
from packaging.version import InvalidVersion, Version

BUILDKITE_ORG_SLUG = "cyberwave"
INTERNAL_PYTHON_REGISTRY_SLUG = "cyberwave-internal-python"


def _normalize_service_channel(channel: str | None) -> str:
    """Return a normalized service release channel."""
    normalized_channel = (channel or "stable").strip().lower()
    if normalized_channel not in {"stable", "dev", "staging"}:
        raise ValueError(f"Unsupported channel: {normalized_channel}")
    return normalized_channel


def _pip_version_matches_channel(version: Version, channel: str) -> bool:
    """Return whether ``version`` belongs to the selected release channel."""
    normalized_channel = _normalize_service_channel(channel)
    if normalized_channel == "stable":
        return not version.is_prerelease and not version.is_devrelease
    if normalized_channel == "dev":
        return version.is_devrelease and version.pre is None
    return version.pre is not None and version.pre[0] == "rc" and not version.is_devrelease


def _validate_pip_channel_version(package_name: str, version_text: str, channel: str) -> Version:
    """Parse and validate an explicit pip version against the selected channel."""
    normalized_channel = _normalize_service_channel(channel)
    try:
        version = Version(version_text)
    except InvalidVersion as exc:
        raise ValueError(f"Invalid PEP 440 version '{version_text}' for {package_name}.") from exc

    if not _pip_version_matches_channel(version, normalized_channel):
        raise ValueError(
            f"Version '{version}' does not match the selected '{normalized_channel}' channel "
            f"for {package_name}."
        )

    return version


def _buildkite_python_registry_index_url(registry_slug: str, read_token: str | None = None) -> str:
    """Return the Buildkite Python simple index URL for ``registry_slug``."""
    if read_token:
        return (
            f"https://buildkite:{read_token}@packages.buildkite.com/"
            f"{BUILDKITE_ORG_SLUG}/{registry_slug}/pypi/simple"
        )
    return f"https://packages.buildkite.com/{BUILDKITE_ORG_SLUG}/{registry_slug}/pypi/simple"


def _buildkite_python_registry_slug(package_name: str) -> str:
    """Return the Buildkite Python registry slug for a package."""
    return f"{package_name}-python"


def _resolve_buildkite_python_registry_slug(package_name: str, channel: str) -> str:
    """Return the shared prerelease registry slug or per-package stable slug."""
    normalized_channel = _normalize_service_channel(channel)
    if normalized_channel == "stable":
        return _buildkite_python_registry_slug(package_name)
    return INTERNAL_PYTHON_REGISTRY_SLUG


def _select_pip_version_for_channel(
    versions: list[Version], *, package_name: str, channel: str
) -> Version:
    """Choose the highest available version that matches ``channel``."""
    normalized_channel = _normalize_service_channel(channel)
    matching_versions = [
        version for version in versions if _pip_version_matches_channel(version, normalized_channel)
    ]
    if not matching_versions:
        raise ValueError(
            f"No versions matching the '{normalized_channel}' channel are available for "
            f"{package_name}."
        )
    return max(matching_versions)


def _extract_version_from_distribution_filename(filename: str, package_name: str) -> Version | None:
    """Best-effort parse a PEP 440 version from a wheel or sdist filename."""
    try:
        if filename.endswith(".whl"):
            parsed_name, parsed_version, _, _ = parse_wheel_filename(filename)
        else:
            parsed_name, parsed_version = parse_sdist_filename(filename)
    except (InvalidVersion, ValueError):
        return None

    if canonicalize_name(parsed_name) != canonicalize_name(package_name):
        return None
    return parsed_version


def _fetch_available_simple_index_versions(index_url: str, package_name: str) -> list[Version]:
    """Fetch and parse available versions from a PEP 503-style simple index page."""
    normalized_name = canonicalize_name(package_name)
    project_url = f"{index_url.rstrip('/')}/{normalized_name}/"
    try:
        with urllib.request.urlopen(project_url) as response:
            html = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to query available versions for {package_name}: {exc}") from exc

    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    parsed_versions: set[Version] = set()
    for href in hrefs:
        parsed_href = urllib.parse.urlsplit(href)
        filename = urllib.parse.unquote(Path(parsed_href.path).name)
        if not filename:
            continue
        parsed_version = _extract_version_from_distribution_filename(filename, package_name)
        if parsed_version is not None:
            parsed_versions.add(parsed_version)

    if not parsed_versions:
        raise RuntimeError(f"No valid PEP 440 versions were found for {package_name}.")

    return sorted(parsed_versions)
