"""CLI commands for Cyberwave."""

from .camera import camera
from .config_dir import config_dir
from .configure import configure
from .edge import edge
from .environment import environment
from .login import login
from .logout import logout
from .model import model
from .plugin import plugin
from .scan import scan
from .so101 import so101
from .twin import twin
from .workflow import workflow

__all__ = [
    "camera",
    "config_dir",
    "configure",
    "edge",
    "environment",
    "login",
    "logout",
    "model",
    "plugin",
    "scan",
    "so101",
    "twin",
    "workflow",
]
