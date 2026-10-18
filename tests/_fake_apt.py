"""A scripted dpkg/apt for the ``update`` command tests."""

import subprocess


class FakeApt:
    """Answers the dpkg/apt queries that ``update_edge_core`` and ``update_cli`` make."""

    def __init__(self, *, installed=None, candidate=None, held=()):
        self.installed = dict(installed or {})  # package -> version
        self.candidate = candidate
        self.held = set(held)
        self.calls: list[list[str]] = []

    def run(self, cmd, **_kwargs):
        self.calls.append(list(cmd))
        if cmd[0] == "dpkg-query":
            version = self.installed.get(cmd[-1])
            if version is None:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout=f"installed {version}", stderr="")
        if cmd[:2] == ["apt-cache", "policy"]:
            candidate = self.candidate or "(none)"
            stdout = f"{cmd[-1]}:\n  Installed: x\n  Candidate: {candidate}\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        if cmd[:2] == ["apt-mark", "showhold"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(self.held), stderr="")
        if cmd[1:] == ["__ensure-systemd-unit"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["dpkg", "--compare-versions"]:
            newer = tuple(map(int, cmd[2].split("."))) > tuple(map(int, cmd[4].split(".")))
            return subprocess.CompletedProcess(cmd, 0 if newer else 1)
        raise AssertionError(f"unexpected command: {cmd}")
