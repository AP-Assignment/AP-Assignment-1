# -*- coding: utf-8 -*-
"""Tests for Two-Factor Authentication (2FA) functionality."""

import pytest
import pyotp
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import User
from app.security import hash_password

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def SessionLocal():
    """In-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@pytest.fixture()
def db(SessionLocal):
    """Database session for a single test."""
    session = SessionLocal()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


def _create_user(db, email="user@example.com", name="Test User", with_2fa=False):
    """Create a test user."""
    user = User(
        name=name,
        email=email,
        password_hash=hash_password("Password123!"),
        team="Test Team",
        role="user",
        status="active",
        manager_email="manager@example.com",
        two_fa_enabled=with_2fa,
    )
    if with_2fa:
        user.generate_two_fa_secret()
    db.add(user)
    db.flush()
    return user


# ---------------------------------------------------------------------------
# Tests: User Model 2FA Methods
# ---------------------------------------------------------------------------


def test_user_generate_two_fa_secret(db):
    """Test generating a 2FA secret."""
    user = _create_user(db)
    assert user.two_fa_secret is None
    
    secret = user.generate_two_fa_secret()
    assert secret is not None
    assert len(secret) == 32  # base32 encoded
    assert user.two_fa_secret == secret


def test_user_get_totp(db):
    """Test getting TOTP object from user."""
    user = _create_user(db, with_2fa=True)
    totp = user.get_totp()
    
    assert isinstance(totp, pyotp.TOTP)
    # Verify it generates a token
    token = totp.now()
    assert len(token) == 6
    assert token.isdigit()


def test_user_get_totp_without_secret(db):
    """Test getting TOTP fails without secret."""
    user = _create_user(db, with_2fa=False)
    user.two_fa_secret = None
    
    with pytest.raises(ValueError):
        user.get_totp()


def test_user_verify_totp_valid(db):
    """Test verifying a valid TOTP token."""
    user = _create_user(db, with_2fa=True)
    token = user.get_totp().now()
    
    assert user.verify_totp(token) is True


def test_user_verify_totp_invalid(db):
    """Test rejecting an invalid TOTP token."""
    user = _create_user(db, with_2fa=True)
    
    assert user.verify_totp("000000") is False
    assert user.verify_totp("abc123") is False
    assert user.verify_totp("") is False


def test_user_verify_totp_no_secret(db):
    """Test verification fails without 2FA enabled."""
    user = _create_user(db, with_2fa=False)
    user.two_fa_secret = None
    
    assert user.verify_totp("123456") is False


def test_user_get_provisioning_uri(db):
    """Test generating provisioning URI for QR code."""
    user = _create_user(db, email="test@example.com", with_2fa=True)
    uri = user.get_provisioning_uri()
    
    assert "otpauth://totp/" in uri
    # Email is URL-encoded in the URI
    assert "test%40example.com" in uri
    assert "AP%20Assignment%20System" in uri
    assert user.two_fa_secret in uri


def test_user_get_provisioning_uri_without_secret(db):
    """Test provisioning URI fails without secret."""
    user = _create_user(db, with_2fa=False)
    user.two_fa_secret = None
    
    with pytest.raises(ValueError):
        user.get_provisioning_uri()


# ---------------------------------------------------------------------------
# Tests: TOTP Token Lifecycle
# ---------------------------------------------------------------------------


def test_totp_tokens_are_valid_for_current_time(db):
    """Test that TOTP tokens work for current time."""
    user = _create_user(db, with_2fa=True)
    
    # Get current token
    token = user.get_totp().now()
    
    # Should verify immediately
    assert user.verify_totp(token) is True


def test_totp_token_format(db):
    """Test that TOTP tokens are 6 digits."""
    user = _create_user(db, with_2fa=True)
    
    # Get token
    token = user.get_totp().now()
    
    assert len(token) == 6
    assert token.isdigit()


# ---------------------------------------------------------------------------
# Tests: Multiple Users with Independent 2FA
# ---------------------------------------------------------------------------


def test_multiple_users_have_different_secrets(db):
    """Test that different users have different 2FA secrets."""
    user1 = _create_user(db, email="user1@example.com", with_2fa=True)
    user2 = _create_user(db, email="user2@example.com", with_2fa=True)
    
    assert user1.two_fa_secret != user2.two_fa_secret


def test_user_tokens_not_valid_for_other_users(db):
    """Test that a token from one user doesn't work for another."""
    user1 = _create_user(db, email="user1@example.com", with_2fa=True)
    user2 = _create_user(db, email="user2@example.com", with_2fa=True)
    
    user1_token = user1.get_totp().now()
    
    # User2 should not be able to use user1's token
    assert user2.verify_totp(user1_token) is False


# ---------------------------------------------------------------------------
# Tests: 2FA Status Management
# ---------------------------------------------------------------------------


def test_user_can_enable_2fa(db):
    """Test enabling 2FA on a user."""
    user = _create_user(db, with_2fa=False)
    assert user.two_fa_enabled is False
    assert user.two_fa_secret is None
    
    user.generate_two_fa_secret()
    user.two_fa_enabled = True
    
    assert user.two_fa_enabled is True
    assert user.two_fa_secret is not None


def test_user_can_disable_2fa(db):
    """Test disabling 2FA on a user."""
    user = _create_user(db, with_2fa=True)
    assert user.two_fa_enabled is True
    assert user.two_fa_secret is not None
    
    user.two_fa_enabled = False
    user.two_fa_secret = None
    
    assert user.two_fa_enabled is False
    assert user.two_fa_secret is None


def test_new_secret_replaces_old_secret(db):
    """Test that generating a new secret overwrites the old one."""
    user = _create_user(db, with_2fa=True)
    old_secret = user.two_fa_secret
    
    new_secret = user.generate_two_fa_secret()
    
    assert new_secret != old_secret
    assert user.two_fa_secret == new_secret


# ---------------------------------------------------------------------------
# Tests: Token Edge Cases
# ---------------------------------------------------------------------------


def test_verify_totp_with_extra_whitespace(db):
    """Test token verification strips whitespace."""
    user = _create_user(db, with_2fa=True)
    token = user.get_totp().now()
    
    # Token with spaces should now verify (whitespace is stripped)
    assert user.verify_totp(f" {token} ") is True


def test_verify_totp_with_invalid_length(db):
    """Test token verification rejects wrong length."""
    user = _create_user(db, with_2fa=True)
    
    assert user.verify_totp("12345") is False  # 5 digits
    assert user.verify_totp("1234567") is False  # 7 digits


def test_provisioning_uri_contains_user_email(db):
    """Test that provisioning URI contains the user's email."""
    user = _create_user(db, email="alice@company.com", with_2fa=True)
    uri = user.get_provisioning_uri()
    
    # Email should be URL-encoded but still present
    assert "alice%40company.com" in uri


def test_provisioning_uri_consistent_for_same_secret(db):
    """Test that provisioning URI is consistent for the same secret."""
    user = _create_user(db, with_2fa=True)
    
    uri1 = user.get_provisioning_uri()
    uri2 = user.get_provisioning_uri()
    
    assert uri1 == uri2


# ---------------------------------------------------------------------------
# Tests: Security Features (Whitespace Stripping, Clock Drift)
# ---------------------------------------------------------------------------


def test_verify_totp_strips_leading_whitespace(db):
    """Test that leading whitespace is stripped."""
    user = _create_user(db, with_2fa=True)
    token = user.get_totp().now()
    
    # Should verify with leading whitespace
    assert user.verify_totp(f"  {token}") is True


def test_verify_totp_strips_trailing_whitespace(db):
    """Test that trailing whitespace is stripped."""
    user = _create_user(db, with_2fa=True)
    token = user.get_totp().now()
    
    # Should verify with trailing whitespace
    assert user.verify_totp(f"{token}  ") is True


def test_verify_totp_strips_mixed_whitespace(db):
    """Test that mixed leading/trailing whitespace is stripped."""
    user = _create_user(db, with_2fa=True)
    token = user.get_totp().now()
    
    # Should verify with both leading and trailing whitespace
    assert user.verify_totp(f"  {token}  ") is True


def test_verify_totp_with_newlines(db):
    """Test that newlines are stripped (common when pasting)."""
    user = _create_user(db, with_2fa=True)
    token = user.get_totp().now()
    
    # Should verify with embedded newline (common paste scenario)
    assert user.verify_totp(f"\n{token}\n") is True
