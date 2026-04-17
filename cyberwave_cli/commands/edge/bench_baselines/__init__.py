"""Per-device-class baselines shipped with the CLI.

The JSON files alongside this module are loaded by ``cyberwave edge bench``
via ``importlib.resources``.  Each file name is the slug returned by
``_detect_device_class()`` (for example ``jetson-orin-nano.json``) with a
``generic-{arch}.json`` fallback when no exact match is found.

Baselines marked ``"provisional": true`` are rough placeholders; replace them
by running ``cyberwave edge bench --save-baseline <device>.json`` on real
reference hardware.
"""
