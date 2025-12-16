"""CLI commands for Cyberwave."""

from .camera import camera
from .login import login
from .logout import logout
from .so101 import so101

__all__ = ["camera", "login", "logout", "so101"]
