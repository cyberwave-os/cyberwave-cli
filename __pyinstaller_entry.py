"""PyInstaller entry point for Cyberwave CLI.

One binary serves two names. Docker runs ``docker-credential-cyberwave`` for
the registries configured by ``cyberwave auth configure-docker``; the Debian
package ships that name as a symlink to ``cyberwave`` so the helper exists
wherever the CLI does, not only under ``pip install``.
"""
import pathlib
import sys

if pathlib.Path(sys.argv[0]).stem == "docker-credential-cyberwave":
    from cyberwave_cli.docker_credentials import main
else:
    from cyberwave_cli.main import main

if __name__ == "__main__":
    main()
