#!/usr/bin/env python3
"""
One-liner installer for Cyberwave CLI with automatic PATH configuration.
Handles different Python/pip configurations automatically.
"""

import subprocess
import sys
import os
import shutil


def find_pip_command():
    """Find the best pip command to use."""
    # Try different pip commands in order of preference
    pip_candidates = [
        # Use the same Python executable that's running this script
        [sys.executable, "-m", "pip"],
        # Common pip commands
        "pip3",
        "pip", 
        # Alternative Python commands
        "python3 -m pip",
        "python -m pip",
    ]
    
    for pip_cmd in pip_candidates:
        try:
            if isinstance(pip_cmd, list):
                # Test with subprocess for list commands
                result = subprocess.run(
                    pip_cmd + ["--version"], 
                    capture_output=True, 
                    text=True, 
                    timeout=10
                )
                if result.returncode == 0:
                    return pip_cmd
            else:
                # Test with shutil.which for string commands
                if "python" in pip_cmd:
                    # Test the full command
                    result = subprocess.run(
                        pip_cmd.split() + ["--version"],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    if result.returncode == 0:
                        return pip_cmd.split()
                else:
                    # Test if the command exists
                    if shutil.which(pip_cmd):
                        result = subprocess.run(
                            [pip_cmd, "--version"],
                            capture_output=True,
                            text=True,
                            timeout=10
                        )
                        if result.returncode == 0:
                            return [pip_cmd]
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
            continue
    
    return None


def run_pip_install(pip_cmd, packages, description):
    """Run pip install with the given pip command."""
    print(f"🔄 {description}...")
    print(f"📦 Using: {' '.join(pip_cmd)} install {packages}")
    
    try:
        cmd = pip_cmd + ["install"] + packages.split()
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        print(f"✅ {description} completed successfully")
        return True
    except subprocess.TimeoutExpired:
        print(f"❌ {description} timed out after 5 minutes")
        return False
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} failed:")
        print(f"   Command: {' '.join(cmd)}")
        print(f"   Return code: {e.returncode}")
        if e.stderr:
            print(f"   Error: {e.stderr.strip()}")
        if e.stdout:
            print(f"   Output: {e.stdout.strip()}")
        return False
    except Exception as e:
        print(f"❌ {description} failed with unexpected error: {e}")
        return False


def check_python_version():
    """Check if Python version is compatible."""
    if sys.version_info < (3, 8):
        print(f"❌ Python {sys.version_info.major}.{sys.version_info.minor} is not supported.")
        print("   Cyberwave CLI requires Python 3.8 or higher.")
        return False
    
    print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} detected")
    return True


def main():
    print("🚀 Cyberwave CLI Installer")
    print("=" * 50)
    
    # Step 0: Check Python version
    if not check_python_version():
        sys.exit(1)
    
    # Step 1: Find pip command
    print("\n🔍 Detecting pip installation method...")
    pip_cmd = find_pip_command()
    
    if not pip_cmd:
        print("❌ No working pip installation found!")
        print("\n💡 Please install pip first:")
        print("   • macOS: python3 -m ensurepip --upgrade")
        print("   • Ubuntu/Debian: sudo apt install python3-pip")
        print("   • CentOS/RHEL: sudo yum install python3-pip")
        print("   • Windows: python -m ensurepip --upgrade")
        sys.exit(1)
    
    print(f"✅ Found pip: {' '.join(pip_cmd)}")
    
    # Step 2: Install packages
    print("\n📦 Installing Cyberwave CLI...")
    if not run_pip_install(
        pip_cmd,
        "cyberwave-cli[robotics]",
        "Installing Cyberwave CLI with robotics support"
    ):
        print("\n🔄 Trying fallback installation without robotics extras...")
        if not run_pip_install(
            pip_cmd,
            "cyberwave-cli cyberwave-robotics-integrations",
            "Installing Cyberwave CLI and robotics integrations separately"
        ):
            print("\n🔄 Trying minimal installation...")
            if not run_pip_install(
                pip_cmd,
                "cyberwave-cli",
                "Installing Cyberwave CLI only"
            ):
                print("\n❌ All installation methods failed!")
                print("📋 Please try manual installation:")
                print(f"   {' '.join(pip_cmd)} install cyberwave-cli")
                sys.exit(1)
            else:
                print("\n⚠️  Installed CLI only. You may want to install robotics support later:")
                print(f"   {' '.join(pip_cmd)} install cyberwave-robotics-integrations")
    
    # Step 3: Run setup
    print("\n🛠️  Configuring PATH...")
    try:
        # Import and run setup after installation
        import cyberwave_cli.setup_utils
        success = cyberwave_cli.setup_utils.setup_cyberwave_cli()
        
        if success:
            print("\n🎉 Installation completed successfully!")
            print("\n📋 Next steps:")
            print("1. Restart your terminal (or run: source ~/.zshrc)")
            print("2. Test with: cyberwave version")
            print("3. Authenticate: cyberwave auth login --backend-url YOUR_URL --frontend-url YOUR_URL")
            print("\n🔗 Documentation: https://cyberwave.com/docs")
        else:
            print("\n⚠️  Installation completed but PATH configuration may need manual setup.")
            print("🔧 Try running: python3 -c 'import cyberwave_cli.setup_utils; cyberwave_cli.setup_utils.setup_cyberwave_cli()'")
            
    except ImportError as e:
        print(f"❌ Could not import setup utilities: {e}")
        print("📋 Manual PATH configuration required - see documentation:")
        print("   https://github.com/cyberwave-os/cyberwave-cli#path-configuration-if-needed")
        # Don't exit with error - installation was successful
    except Exception as e:
        print(f"⚠️  Setup utilities encountered an error: {e}")
        print("📋 You can configure PATH manually or try:")
        print("   cyberwave setup")


if __name__ == "__main__":
    main()
