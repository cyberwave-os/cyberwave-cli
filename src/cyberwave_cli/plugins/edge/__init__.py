"""
Cyberwave Edge v2 - Integrated CLI Plugin

A next-generation edge computing platform integrated into the Cyberwave CLI.
Features dynamic configuration from backend services, modular processor architecture,
and robust device management - all managed through the unified CLI interface.

Key Features:
- Dynamic configuration from backend services
- Modular processor architecture (CV, robotics, ML, custom)
- Scalable driver framework for any device type  
- Integrated CLI management and deployment
- Backward compatibility with v1 edge nodes

This plugin replaces the standalone cyberwave-edge package and provides
unified installation and management through the cyberwave CLI.
"""

__version__ = "2.0.0"
__author__ = "Cyberwave Team"

# Core imports for CLI integration
from .core.node import EdgeNode
from .config.manager import ConfigManager
from .app import edge_app

__all__ = [
    "EdgeNode",
    "ConfigManager", 
    "edge_app",
    "__version__"
]
