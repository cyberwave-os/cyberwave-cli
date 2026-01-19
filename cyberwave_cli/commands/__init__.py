"""CLI commands for Cyberwave."""

from .camera import camera
from .configure import configure
from .connect import connect
from .edge import edge
from .environment import environment
from .login import login
from .logout import logout
from .model import model
from .pair import pair
from .plugin import plugin
from .scan import scan
from .so101 import so101
from .twin import twin
from .workflow import workflow

__all__ = [
    "camera",
    "configure",
    "connect",
    "edge",
    "environment",
    "login",
    "logout",
    "model",
    "pair",
    "plugin",
    "scan",
    "so101",
    "twin",
    "workflow",
]
