"""CLI commands for Cyberwave."""

from __future__ import annotations

from .camera import camera
from .compute import compute
from .completion import completion
from .config_dir import config_dir
from .configure import configure
from .edge import edge
from .environment import environment
from .login import login
from .logout import logout
from .manifest import manifest
from .model import model
from .pair import pair
from .plugin import plugin
from .scan import scan
from .so101 import so101
from .twin import twin
from .workflow import workflow
from .worker import worker

__all__ = [
    "camera",
    "compute",
    "completion",
    "config_dir",
    "configure",
    "edge",
    "environment",
    "login",
    "logout",
    "manifest",
    "model",
    "pair",
    "plugin",
    "scan",
    "so101",
    "twin",
    "workflow",
    "worker",
]
