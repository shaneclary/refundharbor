#!/usr/bin/env python3
# setup.py — interactive setup wizard for DenseWealth

import os
import sys
from pathlib import Path


def print_banner():
    print("\n" + "=" * 60)
    print("📋 DENSEWEALTH SETUP WIZARD")
    print("=" * 60 + "\n")


def check_python_version():
    """Ensure Python 3.10+"""
    if sys.version_info < (3, 10):
        print("❌ Python 3.10+ required")
        print(f"   Current: Python {sys.version_info.major}.{sys.version_info.minor}")
        sys.exit(1)
    print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor} detected")


def install_dependencies():
    """Install required packages."""
    print("\n📦 Installing dependencies...")

    requirements_file = Path(__file__).parent / "requirements.txt"
    if not requirements_file.exists():
        print("❌ requirements.txt not found")
        return False

    import subprocess

    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(requirements_file)],
            check=True,
            capture_output=True,
        )
        print("✅ Dependencies installed")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install dependencies: {e}")
        return False


def create_env_file():
    """Create .env file from example."""
    env_file = Path(__file__).parent / ".env"
    example_file = Path(__file__).parent / ".env.example"

    if env_file.exists():
        overwrite = input("\n.env file already exists. Overwrite? (y/N): ").strip().lower()
        if overwrite != "y":
            print("Keeping existing .env file")
            return True

    try:
        example_file.read_text()
        env_file.write_text(example_file.read_text())
        print("✅ Created .env file")
        return True
    except Exception as e:
        print(f"❌ Failed to create .env: {e}")
        return False


def configure_wallets():
    """Guide user through wallet configuration."""
    print("\n" + "=" * 60)
    print("👛 WALLET CONFIGURATION")
    print("=" * 60)

    config_file = Path(__file__).parent / "config.py"
    config_content = config_file.read_text()

    print("\nTo start copy-trading, you need to add wallet addresses to track.")
    print("\nWhere to find good traders:")
    print("  • https://polymarket.com/leaderboard")
    print("  • Twitter/X — search for Polymarket whales")
    print("  • Dune Analytics — query top performers")

    print("\nExample wallet addresses:")
    print("  0x1234567890abcdef1234567890abcdef12345678")
    print("  0xabcdefabcdefabcdefabcdefabcdefabcdefabcd")

    edit_now = input("\nWould you like to open config.py to add wallets now? (Y/n): ").strip().lower()

    if edit_now != "n":
        print("\n📝 Edit config.py and add wallet addresses to TARGET_WALLETS list")
        print(f"   File location: {config_file}")

        # Try to open in default editor
        try:
            if sys.platform == "win32":
                os.startfile(config_file)
            elif sys.platform == "darwin":
                os.system(f"open {config_file}")
            else:
                os.system(f"xdg-open {config_file}")

            input("\nPress Enter after you've added wallet addresses...")
        except Exception:
            print(f"\nManually edit: {config_file}")
            input("Press Enter when done...")

    print("✅ Wallet configuration ready")


def test_configuration():
    """Test the configuration."""
    print("\n" + "=" * 60)
    print("🧪 TESTING CONFIGURATION")
    print("=" * 60 + "\n")

    try:
        from config import TARGET_WALLETS

        if not TARGET_WALLETS:
            print("⚠️  No wallets configured yet")
            print("   Add wallet addresses to config.py before running")
            return False

        print(f"✅ Found {len(TARGET_WALLETS)} wallet(s) to track")
        return True

    except ImportError as e:
        print(f"❌ Configuration error: {e}")
        return False


def print_next_steps():
    """Print final instructions."""
    print("\n" + "=" * 60)
    print("🎉 SETUP COMPLETE!")
    print("=" * 60 + "\n")

    print("Next steps:")
    print("\n1. Make sure you've added wallet addresses to config.py")
    print("   TARGET_WALLETS = ['0x...', '0x...']")
    print("\n2. Start paper trading:")
    print("   python main.py")
    print("\n3. Monitor performance:")
    print("   python stats.py")
    print("\n4. Check logs for activity")

    print("\n" + "=" * 60)
    print("Documentation: See README.md for full guide")
    print("=" * 60 + "\n")


def main():
    print_banner()

    # Step 1: Check Python version
    check_python_version()

    # Step 2: Install dependencies
    if not install_dependencies():
        print("\n❌ Setup failed at dependency installation")
        sys.exit(1)

    # Step 3: Create .env file
    if not create_env_file():
        print("\n❌ Setup failed at .env creation")
        sys.exit(1)

    # Step 4: Configure wallets
    configure_wallets()

    # Step 5: Test configuration
    test_configuration()

    # Final instructions
    print_next_steps()


if __name__ == "__main__":
    main()
