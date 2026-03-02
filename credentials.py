# credentials.py — Encrypted credential storage for API keys
#
# Uses Fernet (AES-128-CBC) with PBKDF2 key derivation.
# Each credential has a unique salt for key derivation.

import base64
import hashlib
import logging
import os
import secrets
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

log = logging.getLogger(__name__)

# Master key from environment (must be set for credential operations)
MASTER_KEY = os.getenv("DENSEWEALTH_MASTER_KEY", "")


class CredentialError(Exception):
    """Raised when credential operations fail."""
    pass


def _derive_key(master_key: str, salt: bytes) -> bytes:
    """
    Derive a Fernet-compatible key from master key + salt using PBKDF2.
    """
    if not master_key:
        raise CredentialError("DENSEWEALTH_MASTER_KEY not set")

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,  # OWASP 2023 recommendation
    )
    key = kdf.derive(master_key.encode("utf-8"))
    return base64.urlsafe_b64encode(key)


def generate_salt() -> bytes:
    """Generate a cryptographically secure random salt."""
    return secrets.token_bytes(16)


def encrypt_credential(plaintext: str, master_key: str = "") -> tuple[str, str]:
    """
    Encrypt a credential value.

    Args:
        plaintext: The secret value to encrypt (e.g., API key)
        master_key: Override master key (defaults to env var)

    Returns:
        Tuple of (encrypted_value_base64, salt_base64)

    Raises:
        CredentialError: If master key is not set
    """
    key = master_key or MASTER_KEY
    if not key:
        raise CredentialError("DENSEWEALTH_MASTER_KEY not set")

    salt = generate_salt()
    derived_key = _derive_key(key, salt)
    fernet = Fernet(derived_key)

    encrypted = fernet.encrypt(plaintext.encode("utf-8"))

    return (
        base64.urlsafe_b64encode(encrypted).decode("ascii"),
        base64.urlsafe_b64encode(salt).decode("ascii"),
    )


def decrypt_credential(
    encrypted_b64: str,
    salt_b64: str,
    master_key: str = "",
) -> str:
    """
    Decrypt a credential value.

    Args:
        encrypted_b64: Base64-encoded encrypted value
        salt_b64: Base64-encoded salt used during encryption
        master_key: Override master key (defaults to env var)

    Returns:
        Decrypted plaintext

    Raises:
        CredentialError: If decryption fails (wrong key, corrupted data, etc.)
    """
    key = master_key or MASTER_KEY
    if not key:
        raise CredentialError("DENSEWEALTH_MASTER_KEY not set")

    try:
        salt = base64.urlsafe_b64decode(salt_b64)
        encrypted = base64.urlsafe_b64decode(encrypted_b64)

        derived_key = _derive_key(key, salt)
        fernet = Fernet(derived_key)

        decrypted = fernet.decrypt(encrypted)
        return decrypted.decode("utf-8")
    except InvalidToken:
        raise CredentialError("Decryption failed: invalid key or corrupted data")
    except Exception as e:
        raise CredentialError(f"Decryption failed: {e}")


def is_master_key_set() -> bool:
    """Check if the master encryption key is configured."""
    return bool(MASTER_KEY)


def generate_master_key() -> str:
    """
    Generate a new master key suitable for DENSEWEALTH_MASTER_KEY.

    Returns a 32-byte random string encoded as URL-safe base64.
    This should be stored securely in the environment.
    """
    return secrets.token_urlsafe(32)


def mask_credential(value: str, show_chars: int = 4) -> str:
    """
    Mask a credential for display, showing only first/last N chars.
    Example: "sk-abc123xyz789" -> "sk-a***789"
    """
    if len(value) <= show_chars * 2:
        return "*" * len(value)
    return value[:show_chars] + "***" + value[-show_chars:]


# ── Credential Store Interface ─────────────────────────────────────────────
# These functions work with the database to store/retrieve credentials.


def store_credential(
    account_id: int,
    platform: str,
    credential_type: str,
    value: str,
) -> bool:
    """
    Encrypt and store a credential for an account.

    Args:
        account_id: Trading account ID
        platform: Platform name (e.g., "polymarket_global", "polymarket_us")
        credential_type: Type of credential (e.g., "api_key", "api_secret", "private_key")
        value: The secret value to store

    Returns:
        True if stored successfully
    """
    from db import store_account_credential

    encrypted_value, salt = encrypt_credential(value)
    store_account_credential(
        account_id=account_id,
        platform=platform,
        credential_type=credential_type,
        encrypted_value=encrypted_value,
        nonce=salt,
    )

    log.info(
        "Stored credential: account=%d platform=%s type=%s",
        account_id, platform, credential_type
    )
    return True


def retrieve_credential(
    account_id: int,
    platform: str,
    credential_type: str,
) -> Optional[str]:
    """
    Retrieve and decrypt a credential for an account.

    Args:
        account_id: Trading account ID
        platform: Platform name
        credential_type: Type of credential

    Returns:
        Decrypted credential value, or None if not found
    """
    from db import get_account_credential

    cred = get_account_credential(account_id, platform, credential_type)
    if not cred:
        return None

    try:
        decrypted = decrypt_credential(cred["encrypted_value"], cred["nonce"])
        log.debug(
            "Retrieved credential: account=%d platform=%s type=%s",
            account_id, platform, credential_type
        )
        return decrypted
    except CredentialError as e:
        log.error("Failed to decrypt credential: %s", e)
        return None


def delete_credential(
    account_id: int,
    platform: str,
    credential_type: str,
) -> bool:
    """
    Delete a stored credential.

    Returns:
        True if deleted, False if not found
    """
    from db import delete_account_credential

    deleted = delete_account_credential(account_id, platform, credential_type)
    if deleted:
        log.info(
            "Deleted credential: account=%d platform=%s type=%s",
            account_id, platform, credential_type
        )
    return deleted


def get_account_credentials_summary(account_id: int) -> list[dict]:
    """
    Get a summary of all credentials for an account (no decryption).

    Returns list of {platform, credential_type, created_at} dicts.
    """
    from db import list_account_credentials
    return list_account_credentials(account_id)


def validate_polymarket_credentials(
    account_id: int,
    platform: str = "polymarket_global",
) -> dict:
    """
    Check if all required Polymarket credentials are stored for an account.

    Returns:
        {
            "complete": bool,
            "missing": ["api_key", ...],
            "present": ["api_secret", ...],
        }
    """
    if platform == "polymarket_global":
        required = ["api_key", "api_secret", "api_passphrase", "private_key"]
    elif platform == "polymarket_us":
        required = ["api_key", "api_secret", "api_passphrase"]
    else:
        required = []

    creds = get_account_credentials_summary(account_id)
    present = [c["credential_type"] for c in creds if c["platform"] == platform]
    missing = [r for r in required if r not in present]

    return {
        "complete": len(missing) == 0,
        "missing": missing,
        "present": present,
    }
