#!/usr/bin/env python3
# healthcheck.py — validate installation and configuration

import sys
from pathlib import Path


def check_python_version():
    """Check Python version."""
    print("🐍 Checking Python version...")
    if sys.version_info < (3, 10):
        print(f"   ❌ Python 3.10+ required (found {sys.version_info.major}.{sys.version_info.minor})")
        return False
    print(f"   ✅ Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    return True


def check_dependencies():
    """Check required packages."""
    print("\n📦 Checking dependencies...")
    required = ["httpx", "dotenv"]
    missing = []

    for package in required:
        try:
            if package == "dotenv":
                __import__("dotenv")
            else:
                __import__(package)
            print(f"   ✅ {package}")
        except ImportError:
            print(f"   ❌ {package} (missing)")
            missing.append(package)

    if missing:
        print(f"\n   Run: pip install -r requirements.txt")
        return False
    return True


def check_env_file():
    """Check .env file exists."""
    print("\n⚙️  Checking configuration files...")
    env_file = Path(__file__).parent / ".env"

    if not env_file.exists():
        print("   ❌ .env file not found")
        print("      Run: cp .env.example .env")
        return False

    print("   ✅ .env file exists")
    return True


def check_config():
    """Check config.py and TARGET_WALLETS."""
    print("\n👛 Checking wallet configuration...")

    try:
        from config import TARGET_WALLETS

        if not TARGET_WALLETS:
            print("   ⚠️  No wallets configured")
            print("      Add wallet addresses to config.py")
            return False

        print(f"   ✅ {len(TARGET_WALLETS)} wallet(s) configured")

        # Validate wallet format
        for wallet in TARGET_WALLETS:
            if not wallet.startswith("0x") or len(wallet) != 42:
                print(f"   ⚠️  Invalid wallet format: {wallet}")
                print("      Wallets should be 42-char hex strings (0x...)")
                return False

        return True

    except ImportError as e:
        print(f"   ❌ Error loading config: {e}")
        return False


def check_mode():
    """Check trading mode."""
    print("\n🎯 Checking trading mode...")

    try:
        import os
        from dotenv import load_dotenv

        load_dotenv()

        mode = os.getenv("POLYMARKET_MODE", "paper").lower()

        if mode == "paper":
            print(f"   ✅ Mode: PAPER (safe for testing)")
        elif mode == "global":
            print(f"   ⚠️  Mode: GLOBAL (live trading - real money!)")
        elif mode == "us":
            print(f"   ⚠️  Mode: US (live trading - real money!)")
        else:
            print(f"   ❌ Invalid mode: {mode}")
            return False

        return True

    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


def check_database():
    """Check if database can be initialized."""
    print("\n💾 Checking database...")

    try:
        from db import init_db

        init_db()
        print("   ✅ Database OK")
        return True

    except Exception as e:
        print(f"   ❌ Database error: {e}")
        return False


def print_summary(checks_passed):
    """Print final summary."""
    print("\n" + "=" * 60)

    if all(checks_passed):
        print("✅ ALL CHECKS PASSED")
        print("=" * 60)
        print("\nYou're ready to start paper trading!")
        print("\nRun: python main.py")
    else:
        print("❌ SOME CHECKS FAILED")
        print("=" * 60)
        print("\nFix the issues above before running.")
        print("\nFor help:")
        print("  • Read QUICKSTART.md")
        print("  • Run: python setup.py")

    print()


def main():
    """Run all health checks."""
    print("\n" + "=" * 60)
    print("🏥 DENSEWEALTH HEALTH CHECK")
    print("=" * 60 + "\n")

    checks = [
        check_python_version(),
        check_dependencies(),
        check_env_file(),
        check_config(),
        check_mode(),
        check_database(),
    ]

    print_summary(checks)

    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
