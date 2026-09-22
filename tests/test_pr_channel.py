"""Tests for the per-pull-request package channel.

A PR build is an unreviewed artifact. Two properties keep it from reaching
anyone who did not ask for it by name: it lives in its own registry, and its
version is rejected by every release channel. These tests pin both, because
losing either one fails silently — an install would simply start picking up
PR builds with no error anywhere.
"""
from __future__ import annotations

import pytest
from _core_module_loader import load_core_module

from cyberwave_cli.pip_registry import (
    PR_PYTHON_REGISTRY_SLUG,
    Version,
    _normalize_service_channel,
    _pip_version_matches_channel,
    _resolve_buildkite_python_registry_slug,
    _select_pip_version_for_channel,
)

PR_BUILD = Version("1.2.3rc45+gabc123def456")
STAGING_BUILD = Version("1.2.3rc45")
DEV_BUILD = Version("1.2.3.dev45")
STABLE_BUILD = Version("1.2.3")


def test_pr_is_a_recognised_channel():
    assert _normalize_service_channel("pr") == "pr"
    assert _normalize_service_channel("PR") == "pr"


def test_pr_channel_matches_only_local_versions():
    assert _pip_version_matches_channel(PR_BUILD, "pr")
    for release in (STAGING_BUILD, DEV_BUILD, STABLE_BUILD):
        assert not _pip_version_matches_channel(release, "pr")


@pytest.mark.parametrize("channel", ["stable", "dev", "staging"])
def test_release_channels_reject_pr_builds(channel):
    """The guard that makes registry separation fail safe rather than silently."""
    assert not _pip_version_matches_channel(PR_BUILD, channel)


def test_release_channels_still_match_their_own_builds():
    assert _pip_version_matches_channel(STABLE_BUILD, "stable")
    assert _pip_version_matches_channel(DEV_BUILD, "dev")
    assert _pip_version_matches_channel(STAGING_BUILD, "staging")


def test_a_pr_build_cannot_outrank_the_staging_release():
    """Without the local-version guard this returns the PR build.

    A PEP 440 local version sorts above the public version it was cut from,
    and selection takes the maximum, so a PR build leaking into the staging
    index would become everyone's staging install.
    """
    assert PR_BUILD > STAGING_BUILD  # the hazard being defended against

    selected = _select_pip_version_for_channel(
        [STAGING_BUILD, PR_BUILD], package_name="cyberwave-edge-core", channel="staging"
    )

    assert selected == STAGING_BUILD


def test_pr_channel_refuses_to_guess_a_version():
    """PR builds from unrelated branches have no meaningful ordering."""
    with pytest.raises(ValueError, match="no default version"):
        _select_pip_version_for_channel(
            [PR_BUILD], package_name="cyberwave-edge-core", channel="pr"
        )


def test_pr_channel_uses_its_own_python_registry():
    slug = _resolve_buildkite_python_registry_slug("cyberwave-edge-core", "pr")

    assert slug == PR_PYTHON_REGISTRY_SLUG
    assert slug != _resolve_buildkite_python_registry_slug("cyberwave-edge-core", "staging")


def test_pr_channel_uses_its_own_deb_registry(monkeypatch):
    core = load_core_module(monkeypatch)

    repo_url, key_url = core._resolve_deb_registry_urls(core.EDGE_CORE_SPEC, "pr")

    assert repo_url == "https://packages.buildkite.com/cyberwave/cyberwave-pr-deb/any/"
    assert key_url == "https://packages.buildkite.com/cyberwave/cyberwave-pr-deb/gpgkey"


def test_pr_channel_has_its_own_deb_package_name(monkeypatch):
    """A distinct package name keeps a PR build off the release package."""
    core = load_core_module(monkeypatch)

    assert core._resolve_edge_core_package_name("pr") == "cyberwave-edge-core-pr"
    assert core._resolve_edge_core_package_name("staging") == "cyberwave-edge-core-staging"


def test_setup_service_refuses_pr_channel_without_a_version(monkeypatch, capsys):
    """Fails before touching any index, identically on apt and pip hosts."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "_migrate_legacy_config_dir", lambda: None)

    def _unexpected(*_args, **_kwargs):
        raise AssertionError("install proceeded without an explicit PR version")

    monkeypatch.setattr(core, "_is_linux", _unexpected)

    assert core.setup_service(core.EDGE_CORE_SPEC, channel="pr", version=None) is False
