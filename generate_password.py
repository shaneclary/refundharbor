#!/usr/bin/env python3
# generate_password.py — generate bcrypt hash for OPERATOR_PASSWORD_HASH
import getpass
import secrets

import bcrypt


def main():
    password = getpass.getpass("Enter operator password: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        print("Passwords do not match.")
        return

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    session_secret = secrets.token_hex(32)

    print("\nAdd these to your .env file:\n")
    print(f"OPERATOR_PASSWORD_HASH={hashed}")
    print(f"SESSION_SECRET={session_secret}")


if __name__ == "__main__":
    main()
