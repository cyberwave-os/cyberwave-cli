#!/usr/bin/env python3
"""
Cyberwave CLI setup utilities for PATH configuration and verification.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple


def find_cyberwave_executable() -> Optional[str]:
    """Find the cyberwave executable in common locations."""
    # First, check if it's already in PATH
    cyberwave_path = shutil.which("cyberwave")
    if cyberwave_path:
        return cyberwave_path
    
    # Common locations to search
    search_paths = [
        # Python framework (macOS)
        f"/Library/Frameworks/Python.framework/Versions/{sys.version_info.major}.{sys.version_info.minor}/bin",
        # User local bin (Linux/macOS)
        os.path.expanduser("~/.local/bin"),
        # Homebrew Python
        "/usr/local/bin",
        "/opt/homebrew/bin",
        # System Python
        "/usr/bin",
        # Virtual environments
        os.path.join(sys.prefix, "bin"),
        os.path.join(sys.exec_prefix, "bin"),
    ]
    
    # Add the directory where current Python executable is located
    python_bin_dir = os.path.dirname(sys.executable)
    if python_bin_dir not in search_paths:
        search_paths.insert(0, python_bin_dir)
    
    for path in search_paths:
        if os.path.exists(path):
            cyberwave_exe = os.path.join(path, "cyberwave")
            if os.path.isfile(cyberwave_exe) and os.access(cyberwave_exe, os.X_OK):
                return cyberwave_exe
    
    return None


def get_shell_config_file() -> Tuple[str, str]:
    """Detect the user's shell and return the appropriate config file."""
    shell = os.environ.get("SHELL", "/bin/bash")
    
    if "zsh" in shell:
        return "zsh", os.path.expanduser("~/.zshrc")
    elif "bash" in shell:
        # Check for .bash_profile first (macOS), then .bashrc (Linux)
        bash_profile = os.path.expanduser("~/.bash_profile")
        bashrc = os.path.expanduser("~/.bashrc")
        if sys.platform == "darwin" and os.path.exists(bash_profile):
            return "bash", bash_profile
        else:
            return "bash", bashrc
    elif "fish" in shell:
        return "fish", os.path.expanduser("~/.config/fish/config.fish")
    else:
        # Default to bash
        return "bash", os.path.expanduser("~/.bashrc")


def is_in_path(directory: str) -> bool:
    """Check if a directory is already in PATH."""
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    return os.path.abspath(directory) in [os.path.abspath(p) for p in path_dirs]


def add_to_path(directory: str, shell_config: str, shell_type: str) -> bool:
    """Add directory to PATH in shell config file."""
    if is_in_path(directory):
        return True
    
    # Create config file if it doesn't exist
    os.makedirs(os.path.dirname(shell_config), exist_ok=True)
    
    # Prepare the export line
    if shell_type == "fish":
        export_line = f'set -gx PATH "{directory}" $PATH\n'
    else:
        export_line = f'export PATH="{directory}:$PATH"\n'
    
    # Check if the line already exists
    try:
        with open(shell_config, 'r') as f:
            content = f.read()
            if directory in content and 'PATH' in content:
                return True  # Already configured
    except FileNotFoundError:
        content = ""
    
    # Add the export line
    try:
        with open(shell_config, 'a') as f:
            f.write(f"\n# Added by Cyberwave CLI setup\n")
            f.write(export_line)
        return True
    except Exception as e:
        print(f"Error writing to {shell_config}: {e}")
        return False


def setup_cyberwave_cli() -> bool:
    """
    Automatically configure Cyberwave CLI PATH if needed.
    Returns True if successful or already configured.
    """
    print("🔍 Checking Cyberwave CLI installation...")
    
    # Find the cyberwave executable
    cyberwave_path = find_cyberwave_executable()
    
    if not cyberwave_path:
        print("❌ Cyberwave executable not found. Please reinstall cyberwave-cli.")
        return False
    
    print(f"✅ Found cyberwave at: {cyberwave_path}")
    
    # Check if it's already accessible
    if shutil.which("cyberwave"):
        print("✅ Cyberwave CLI is already in your PATH!")
        return True
    
    # Get the directory containing the executable
    bin_dir = os.path.dirname(cyberwave_path)
    
    # Detect shell and config file
    shell_type, shell_config = get_shell_config_file()
    print(f"🐚 Detected shell: {shell_type}")
    print(f"📝 Config file: {shell_config}")
    
    # Add to PATH
    print(f"🛠️  Adding {bin_dir} to PATH...")
    
    if add_to_path(bin_dir, shell_config, shell_type):
        print(f"✅ Successfully added to {shell_config}")
        print("🔄 Please restart your terminal or run:")
        print(f"   source {shell_config}")
        print("   cyberwave version")
        return True
    else:
        print("❌ Failed to configure PATH automatically.")
        print("📋 Manual setup required:")
        print(f"   echo 'export PATH=\"{bin_dir}:$PATH\"' >> {shell_config}")
        print(f"   source {shell_config}")
        return False


def verify_installation() -> bool:
    """Verify that cyberwave CLI is working correctly."""
    try:
        result = subprocess.run(
            ["cyberwave", "version"],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            print("✅ Cyberwave CLI is working correctly!")
            return True
        else:
            print(f"❌ Cyberwave CLI test failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print("❌ Cyberwave CLI test timed out")
        return False
    except FileNotFoundError:
        print("❌ Cyberwave CLI not found in PATH")
        return False
    except Exception as e:
        print(f"❌ Error testing cyberwave CLI: {e}")
        return False


def main():
    """Main setup function."""
    print("🚀 Cyberwave CLI Setup")
    print("=" * 40)
    
    success = setup_cyberwave_cli()
    
    if success and shutil.which("cyberwave"):
        print("\n🎉 Setup complete! Testing installation...")
        verify_installation()
    else:
        print("\n⚠️  Setup completed but may require manual steps.")
        print("Please restart your terminal and try 'cyberwave version'")


if __name__ == "__main__":
    main()
