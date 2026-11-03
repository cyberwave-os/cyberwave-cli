"""Version helpers for the Cyberwave CLI."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version

STATIC_VERSION = "0.12.10"

try:
    from ._build_version import BUILD_VERSION
except ImportError:
    BUILD_VERSION = None


def get_version() -> str:
    """Resolve build, installed, or source version in that order."""
    if BUILD_VERSION:
        return BUILD_VERSION

    try:
        return metadata_version("cyberwave-cli")
    except PackageNotFoundError:
        return STATIC_VERSION
    except Exception:
        return STATIC_VERSION


def get_sdk_version() -> str | None:
    """Version of the ``cyberwave`` SDK this CLI runs against, or None if unknown.

    Package metadata first, which is cheap. A PyInstaller bundle carries no
    dist-info, so there the SDK is imported instead.
    """
    try:
        return metadata_version("cyberwave")
    except Exception:
        pass
    try:
        import cyberwave

        return str(cyberwave.__version__)
    except Exception:
        return None
