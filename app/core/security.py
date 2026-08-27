from __future__ import annotations

import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 600_000


class PasswordFormatError(ValueError):
    """Raised when a stored password hash cannot be parsed."""


def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS) -> str:
    """Return a `pbkdf2_sha256$iterations$salt$digest` string."""
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt, iterations)
    return f"{ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def validate_password_hash(stored: str) -> None:
    """Raise PasswordFormatError unless the string is a usable hash.

    Checking the shape is cheap, while deriving a digest is deliberately slow,
    so configuration can be validated at startup without paying that cost.
    """
    _parse(stored)


def verify_password_hash(stored: str, candidate: str) -> bool:
    _, iterations, salt, expected = _parse(stored)
    return hmac.compare_digest(_derive(candidate, salt, iterations), expected)


def _parse(stored: str) -> tuple[str, int, bytes, bytes]:
    parts = stored.split("$")
    if len(parts) != 4:
        raise PasswordFormatError(
            "Password hash must look like pbkdf2_sha256$iterations$salt$digest"
        )
    algorithm, iterations_raw, salt_hex, digest_hex = parts
    if algorithm != ALGORITHM:
        raise PasswordFormatError(f"Unsupported password algorithm: {algorithm}")
    try:
        iterations = int(iterations_raw)
        salt = bytes.fromhex(salt_hex)
        digest = bytes.fromhex(digest_hex)
    except ValueError as error:
        raise PasswordFormatError("Password hash contains invalid values") from error
    if iterations <= 0:
        raise PasswordFormatError("Password hash iterations must be positive")
    return algorithm, iterations, salt, digest


def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def main() -> None:
    """Print a hash for a password typed into the terminal."""
    import getpass

    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat: "):
        raise SystemExit("Passwords do not match")
    if not password:
        raise SystemExit("Password must not be empty")
    print(hash_password(password))


if __name__ == "__main__":
    main()
