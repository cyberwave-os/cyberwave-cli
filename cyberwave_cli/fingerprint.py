"""
Device fingerprinting for Cyberwave CLI.

This module re-exports fingerprinting functions from the Cyberwave SDK
to maintain a single source of truth for device identification.

For documentation, see: cyberwave.fingerprint
"""

# Re-export from SDK to avoid code duplication
from cyberwave.fingerprint import (
    generate_fingerprint,
    get_device_info,
    format_device_info_table,
)

__all__ = [
    "generate_fingerprint",
    "get_device_info",
    "format_device_info_table",
]
