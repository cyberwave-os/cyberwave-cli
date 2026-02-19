#!/bin/bash
# Build script for creating standalone Cyberwave CLI binary

set -e

echo "=== Building Cyberwave CLI ==="

# Ensure we're in the project directory
cd "$(dirname "$0")"

# Check if pyinstaller is installed
if ! command -v pyinstaller &> /dev/null; then
    echo "Installing build dependencies..."
    pip install -e ".[build]"
fi

# Clean previous builds
rm -rf build dist *.spec __pyinstaller_entry.py

# Create a wrapper entry point that uses absolute imports
cat > __pyinstaller_entry.py << 'EOF'
"""PyInstaller entry point for Cyberwave CLI."""
from cyberwave_cli.main import main

if __name__ == "__main__":
    main()
EOF

# Build standalone binary
echo "Building standalone binary..."
pyinstaller \
    --onefile \
    --name cyberwave \
    --add-data "cyberwave_cli/install_docker.sh:cyberwave_cli" \
    --hidden-import cyberwave_cli \
    --hidden-import cyberwave_cli.commands \
    --hidden-import cyberwave_cli.commands.login \
    --hidden-import cyberwave_cli.commands.logout \
    --hidden-import cyberwave_cli.commands.so101 \
    --hidden-import cyberwave_cli.auth \
    --hidden-import cyberwave_cli.config \
    --hidden-import cyberwave_cli.credentials \
    --hidden-import click \
    --hidden-import rich \
    --hidden-import httpx \
    --collect-submodules rich._unicode_data \
    __pyinstaller_entry.py

# Clean up the temporary entry point
rm -f __pyinstaller_entry.py

echo ""
echo "=== Build complete ==="
echo "Binary: dist/cyberwave"
echo ""
echo "Test with: ./dist/cyberwave --help"
