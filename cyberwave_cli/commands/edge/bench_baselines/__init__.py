"""Per-device-class baselines shipped with the CLI.

The JSON files alongside this module are loaded by ``cyberwave edge bench``
via ``importlib.resources``.  Each file name is the slug returned by
``_detect_device_class()`` (for example ``jetson-orin-nano.json`` or
``apple-silicon-m4.json``).

The loader walks the slug from most specific to least specific by stripping
trailing ``-segment`` suffixes, then falls back to ``generic-{arch}.json``.
For example ``apple-silicon-m4`` tries ``apple-silicon-m4.json``, then
``apple-silicon.json``, then ``generic-arm64.json``.  Missing files in the
chain are silently skipped, so a tier can be shipped with *only* the generic
parent file and added later without breaking older CLIs.

Baselines marked ``"provisional": true`` are rough placeholders; replace them
by running ``cyberwave edge bench --save-baseline <device>.json`` on real
reference hardware.
"""
