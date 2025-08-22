#!/usr/bin/env python3
"""
One-liner installer for Cyberwave CLI with automatic PATH configuration.
"""

import subprocess
import sys
import os


def run_command(cmd, description):
    """Run a command and handle errors."""
    print(f"🔄 {description}...")
    try:
        result = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        print(f"✅ {description} completed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} failed:")
        print(f"   Command: {cmd}")
        print(f"   Error: {e.stderr.strip()}")
        return False


def main():
    print("🚀 Cyberwave CLI Installer")
    print("=" * 50)
    
    # Step 1: Install packages
    if not run_command(
        "pip install cyberwave-cli[robotics]",
        "Installing Cyberwave CLI with robotics support"
    ):
        sys.exit(1)
    
    # Step 2: Run setup
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
        else:
            print("\n⚠️  Installation completed but PATH configuration may need manual setup.")
            
    except ImportError as e:
        print(f"❌ Could not import setup utilities: {e}")
        print("📋 Manual PATH configuration required - see documentation.")
        sys.exit(1)


if __name__ == "__main__":
    main()
