import pytest

from app.core.security import (
    PasswordFormatError,
    hash_password,
    validate_password_hash,
    verify_password_hash,
)

# Hashing is deliberately slow, so tests use a low work factor.
ITERATIONS = 1_000


def test_hash_verifies_only_the_original_password() -> None:
    stored = hash_password("правильный пароль", iterations=ITERATIONS)

    assert verify_password_hash(stored, "правильный пароль")
    assert not verify_password_hash(stored, "другой пароль")


def test_the_same_password_hashes_differently_every_time() -> None:
    first = hash_password("пароль", iterations=ITERATIONS)
    second = hash_password("пароль", iterations=ITERATIONS)

    assert first != second
    assert verify_password_hash(second, "пароль")


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "plaintext",
        "pbkdf2_sha256$1000$notahexsalt$00",
        "bcrypt$1000$aa$bb",
        "pbkdf2_sha256$0$aa$bb",
    ],
)
def test_broken_hashes_are_reported_not_silently_accepted(stored: str) -> None:
    with pytest.raises(PasswordFormatError):
        validate_password_hash(stored)


def test_validation_accepts_a_real_hash() -> None:
    validate_password_hash(hash_password("пароль", iterations=ITERATIONS))
