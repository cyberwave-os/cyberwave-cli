"""Tests for ensure_edge_core_importable() sys.path injection."""

import importlib
import sys

import cyberwave_cli.config as config_mod


def _reload_config():
    """Force-reload the real config module to avoid stale stubs."""
    importlib.reload(config_mod)
    return config_mod


def test_adds_path_when_dir_exists(tmp_path):
    """When the deb python dir exists with the package, it's added to sys.path."""
    mod = _reload_config()
    lib_dir = tmp_path / "python"
    (lib_dir / "cyberwave_edge_core").mkdir(parents=True)

    original = mod._EDGE_CORE_DEB_PYTHON_PATH
    try:
        mod._EDGE_CORE_DEB_PYTHON_PATH = str(lib_dir)
        if str(lib_dir) in sys.path:
            sys.path.remove(str(lib_dir))

        mod.ensure_edge_core_importable()

        assert str(lib_dir) in sys.path
        sys.path.remove(str(lib_dir))
    finally:
        mod._EDGE_CORE_DEB_PYTHON_PATH = original


def test_no_op_when_dir_missing():
    """When the deb python dir doesn't exist, sys.path is unchanged."""
    mod = _reload_config()
    original = mod._EDGE_CORE_DEB_PYTHON_PATH
    try:
        mod._EDGE_CORE_DEB_PYTHON_PATH = "/nonexistent/cyberwave-edge-core/python"
        original_path = sys.path.copy()
        mod.ensure_edge_core_importable()
        assert sys.path == original_path
    finally:
        mod._EDGE_CORE_DEB_PYTHON_PATH = original


def test_no_op_when_package_subdir_missing(tmp_path):
    """When the dir exists but cyberwave_edge_core/ is absent, skip."""
    mod = _reload_config()
    lib_dir = tmp_path / "python"
    lib_dir.mkdir()

    original = mod._EDGE_CORE_DEB_PYTHON_PATH
    try:
        mod._EDGE_CORE_DEB_PYTHON_PATH = str(lib_dir)
        original_path = sys.path.copy()
        mod.ensure_edge_core_importable()
        assert sys.path == original_path
    finally:
        mod._EDGE_CORE_DEB_PYTHON_PATH = original


def test_idempotent(tmp_path):
    """Calling twice doesn't duplicate the path entry."""
    mod = _reload_config()
    lib_dir = tmp_path / "python"
    (lib_dir / "cyberwave_edge_core").mkdir(parents=True)

    original = mod._EDGE_CORE_DEB_PYTHON_PATH
    try:
        mod._EDGE_CORE_DEB_PYTHON_PATH = str(lib_dir)
        if str(lib_dir) in sys.path:
            sys.path.remove(str(lib_dir))

        mod.ensure_edge_core_importable()
        mod.ensure_edge_core_importable()

        assert sys.path.count(str(lib_dir)) == 1
        sys.path.remove(str(lib_dir))
    finally:
        mod._EDGE_CORE_DEB_PYTHON_PATH = original


def test_default_path_constant():
    """The default deb path matches the CI layout."""
    mod = _reload_config()
    assert mod._EDGE_CORE_DEB_PYTHON_PATH == "/usr/lib/cyberwave-edge-core/python"
