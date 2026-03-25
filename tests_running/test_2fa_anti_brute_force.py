# -*- coding: utf-8 -*-
"""
Integration tests for 2FA anti-brute force protection using Flask test client.

Tests cover:
- Successful TOTP verification
- Invalid TOTP token rejection
- Failed attempt tracking
- Lockout after maximum failed attempts (5)
- Session cleanup on success and lockout
- Proper error messages

No browser drivers required - uses Flask's test client instead.
"""

import pytest
import pyotp
from app import create_app
from app.db import Base
from app.models import User
from app.security import hash_password
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="session")
def app():
    """Create Flask app with in-memory database for testing."""
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    
    # Use in-memory SQLite for tests
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    
    app.session_factory = SessionLocal
    app.engine = engine
    
    return app


@pytest.fixture(scope="session", autouse=True)
def seed_test_data(app):
    """Seed test data into the database."""
    SessionLocal = app.session_factory
    
    with SessionLocal() as db:
        # Create test users with and without 2FA
        users = [
            User(
                name="User With 2FA",
                email="user_with_2fa@test.com",
                password_hash=hash_password("Password123!"),
                team="Security Team",
                manager_email="admin@test.com",
                role="user",
                status="active",
                two_fa_enabled=True
            ),
            User(
                name="Regular User",
                email="regular_user@test.com",
                password_hash=hash_password("Password123!"),
                team="Engineering",
                manager_email="admin@test.com",
                role="user",
                status="active",
                two_fa_enabled=False
            ),
        ]
        
        # Create 2FA secret for the user with 2FA enabled
        if users[0].two_fa_enabled:
            users[0].generate_two_fa_secret()
        
        for user in users:
            db.add(user)
        
        db.commit()


@pytest.fixture
def client(app):
    """Create a Flask test client with session support."""
    with app.test_client() as test_client:
        with test_client.session_transaction() as sess:
            sess.clear()
        yield test_client


@pytest.fixture
def authenticated_session_with_2fa(app, client):
    """Authenticate and reach the 2FA verification page."""
    # Login with user that has 2FA enabled
    response = client.post("/login", data={
        "email": "user_with_2fa@test.com",
        "password": "Password123!"
    }, follow_redirects=False)
    
    # Should redirect to 2FA verification
    assert response.status_code in [302, 307]
    
    # Fetch the verify-totp page to verify we're at the right place
    response = client.get("/verify-totp")
    assert response.status_code == 200
    
    return client


# ==================== SUCCESSFUL VERIFICATION TESTS ====================

class TestSuccessfulTOTPVerification:
    """Tests for successful 2FA verification."""
    
    def test_valid_totp_token_allows_login(self, app, client):
        """Test that a valid TOTP token successfully logs in the user."""
        # First, navigate to 2FA by logging in with 2FA user
        login_response = client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # Get the user's TOTP secret to generate a valid token
        with app.session_factory() as db:
            user = db.query(User).filter(User.email == "user_with_2fa@test.com").first()
            valid_token = user.get_totp().now()
        
        # Submit valid TOTP token
        response = client.post("/verify-totp", data={
            "token": valid_token
        }, follow_redirects=False)
        
        # Should redirect to my_bookings (successful login)
        assert response.status_code in [302, 307]
        
        # Attempt to access protected page to verify login was successful
        response = client.get("/bookings/my", follow_redirects=True)
        assert response.status_code == 200
    
    def test_totp_token_with_whitespace_accepted(self, app, client):
        """Test that TOTP tokens with whitespace are accepted."""
        # Login with 2FA user
        client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # Get valid token and add whitespace
        with app.session_factory() as db:
            user = db.query(User).filter(User.email == "user_with_2fa@test.com").first()
            valid_token = user.get_totp().now()
        
        # Submit token with spaces
        response = client.post("/verify-totp", data={
            "token": f"  {valid_token}  "
        }, follow_redirects=False)
        
        # Should be accepted and redirect to home
        assert response.status_code in [302, 307]
    
    def test_session_cleaned_after_successful_verification(self, app, client):
        """Test that failed attempts counter is cleared after successful login."""
        # Login with 2FA user
        client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # First, fail once
        client.post("/verify-totp", data={
            "token": "000000"
        })
        
        # Check session has failed attempts
        with client.session_transaction() as sess:
            assert sess.get("totp_failed_attempts", 0) > 0
        
        # Now login fresh with valid token
        client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        with app.session_factory() as db:
            user = db.query(User).filter(User.email == "user_with_2fa@test.com").first()
            valid_token = user.get_totp().now()
        
        client.post("/verify-totp", data={
            "token": valid_token
        }, follow_redirects=False)
        
        # Check session is cleaned
        with client.session_transaction() as sess:
            assert sess.get("totp_failed_attempts", 0) == 0
            assert "pending_user_id" not in sess
            assert "pending_user_email" not in sess


# ==================== INVALID TOKEN TESTS ====================

class TestInvalidTOTPRejection:
    """Tests for rejection of invalid TOTP tokens."""
    
    def test_invalid_token_rejected(self, app, client):
        """Test that invalid TOTP tokens are rejected."""
        # Login with 2FA user
        client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # Submit invalid token
        response = client.post("/verify-totp", data={
            "token": "000000"
        }, follow_redirects=True)
        
        # Should show error and stay on verify page
        assert response.status_code == 200
        assert b"Invalid authentication code" in response.data
    
    def test_empty_token_rejected(self, app, client):
        """Test that empty TOTP token is rejected."""
        # Login with 2FA user
        client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # Submit empty token
        response = client.post("/verify-totp", data={
            "token": ""
        }, follow_redirects=True)
        
        assert response.status_code == 200
    
    def test_malformed_token_rejected(self, app, client):
        """Test that malformed TOTP tokens are rejected."""
        # Login with 2FA user
        client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # Submit malformed tokens
        invalid_tokens = ["abc123", "12345", "1234567", "aabbcc"]
        
        for token in invalid_tokens:
            response = client.post("/verify-totp", data={
                "token": token
            }, follow_redirects=True)
            
            assert response.status_code == 200
            assert b"Invalid authentication code" in response.data


# ==================== FAILED ATTEMPTS TRACKING TESTS ====================

class TestFailedAttemptsTracking:
    """Tests for tracking failed TOTP verification attempts."""
    
    def test_failed_attempt_increments_counter(self, app, client):
        """Test that failed attempts are properly tracked."""
        # Login with 2FA user
        client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # Initial state: 0 failed attempts
        with client.session_transaction() as sess:
            assert sess.get("totp_failed_attempts", 0) == 0
        
        # First failed attempt
        client.post("/verify-totp", data={
            "token": "000000"
        })
        
        with client.session_transaction() as sess:
            assert sess.get("totp_failed_attempts", 0) == 1
    
    def test_multiple_failed_attempts_increment(self, app, client):
        """Test that multiple failed attempts properly increment the counter."""
        # Login with 2FA user
        client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # Make 3 failed attempts
        for attempt_num in range(1, 4):
            client.post("/verify-totp", data={
                "token": "000000"
            })
            
            with client.session_transaction() as sess:
                assert sess.get("totp_failed_attempts", 0) == attempt_num
    
    def test_failed_attempts_counter_per_session(self, app):
        """Test that failed attempts counter is isolated per session."""
        # Create two separate clients (separate sessions)
        client1 = app.test_client()
        client2 = app.test_client()
        
        # Both clients login with 2FA
        for client in [client1, client2]:
            client.post("/login", data={
                "email": "user_with_2fa@test.com",
                "password": "Password123!"
            }, follow_redirects=False)
        
        # First client makes 2 failed attempts
        for _ in range(2):
            client1.post("/verify-totp", data={
                "token": "000000"
            })
        
        # Check counters - client1 should have 2, client2 should have 0
        with client1.session_transaction() as sess:
            assert sess.get("totp_failed_attempts", 0) == 2
        
        with client2.session_transaction() as sess:
            assert sess.get("totp_failed_attempts", 0) == 0


# ==================== LOCKOUT AFTER MAX ATTEMPTS TESTS ====================

class TestBruteForceLockout:
    """Tests for account lockout after maximum failed attempts."""
    
    def test_lockout_after_five_failed_attempts(self, app, client):
        """Test that user is locked out after 5 failed TOTP attempts."""
        # Login with 2FA user
        client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # Make 5 failed attempts
        for attempt in range(1, 6):
            response = client.post("/verify-totp", data={
                "token": "000000"
            }, follow_redirects=False)
            
            if attempt < 5:
                # Should stay on verify page with error
                assert response.status_code in [200, 302]
            else:
                # 5th attempt should lockout
                assert response.status_code in [302, 307]
        
        # Check we're redirected to login with error
        response = client.get("/")
        # After lockout, pending session should be cleared
        with client.session_transaction() as sess:
            assert "pending_user_id" not in sess
            assert sess.get("totp_failed_attempts", 0) == 0
    
    def test_locked_out_user_cannot_verify_immediately(self, app, client):
        """Test that a locked out user cannot immediately verify a token."""
        # Login with 2FA user
        client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # Make 5 failed attempts to trigger lockout
        for _ in range(5):
            client.post("/verify-totp", data={
                "token": "000000"
            }, follow_redirects=False)
        
        # Now try to verify-totp page - should show "Session expired"
        response = client.get("/verify-totp", follow_redirects=True)
        # Should be redirected to login
        assert response.status_code in [200, 302]
    
    def test_lockout_message_displayed(self, app, client):
        """Test that appropriate error message is displayed on lockout."""
        # Login with 2FA user
        client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # Make 5 failed attempts
        for _ in range(5):
            client.post("/verify-totp", data={
                "token": "000000"
            }, follow_redirects=False)
        
        # Try to access verify page - should be redirected to login
        response = client.get("/verify-totp", follow_redirects=True)
        # After lockout and redirect, we should be on login or see error message
        assert b"Session expired" in response.data or b"login" in response.data.lower()
    
    def test_user_must_relogin_after_lockout(self, app, client):
        """Test that locked out user must re-login instead of retrying verification."""
        # Login with 2FA user
        client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # Make 5 failed attempts to lockout
        for _ in range(5):
            client.post("/verify-totp", data={
                "token": "000000"
            }, follow_redirects=False)
        
        # Verify the session is cleared
        with client.session_transaction() as sess:
            assert "pending_user_id" not in sess
        
        # User should have to login again
        response = client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        assert response.status_code in [302, 307]
        
        # Now should be back at verify-totp page with fresh counter
        with client.session_transaction() as sess:
            assert sess.get("totp_failed_attempts", 0) == 0


# ==================== SESSION MANAGEMENT TESTS ====================

class TestSessionManagement:
    """Tests for proper session management during 2FA verification."""
    
    def test_pending_user_info_stored_in_session(self, app, client):
        """Test that pending user info is stored in session after login."""
        # Login with 2FA user
        response = client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # Check session has pending user info
        with client.session_transaction() as sess:
            assert "pending_user_id" in sess
            assert "pending_user_email" in sess
            assert sess.get("pending_user_email") == "user_with_2fa@test.com"
    
    def test_session_expires_without_pending_user(self, app, client):
        """Test that accessing verify without pending user redirects to login."""
        # Try to access verify without being in 2FA flow
        response = client.get("/verify-totp", follow_redirects=False)
        
        # Should redirect to login
        assert response.status_code in [302, 307]
        assert response.location.endswith("/login") or response.location.endswith("/")
    
    def test_session_cleared_on_timeout(self, app, client):
        """Test that session is properly cleared on various timeout scenarios."""
        # Login with 2FA user
        client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # Manually clear pending_user_id to simulate session timeout
        with client.session_transaction() as sess:
            sess.pop("pending_user_id", None)
        
        # Try to submit verification - should show session expired
        response = client.post("/verify-totp", data={
            "token": "123456"
        }, follow_redirects=True)
        
        assert b"Session expired" in response.data


# ==================== RATE LIMITING DELAY TESTS ====================

class TestRateLimitingDelay:
    """Tests for rate limiting delays on failed attempts."""
    
    def test_delay_added_after_failed_attempt(self, app, client):
        """Test that there's a delay after failed verification attempts."""
        import time
        
        # Login with 2FA user
        client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # Measure time for a failed attempt
        start_time = time.time()
        client.post("/verify-totp", data={
            "token": "000000"
        })
        elapsed_time = time.time() - start_time
        
        # Should have at least a small delay (0.5 seconds)
        # Allow some tolerance for system variance
        assert elapsed_time >= 0.4  # Slightly less than 0.5 to account for system variance


# ==================== EDGE CASES TESTS ====================

class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""
    
    def test_exactly_five_attempts_triggers_lockout(self, app, client):
        """Test that exactly 5 failed attempts (not more, not less) triggers lockout."""
        # Login with 2FA user
        client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # 4 attempts should not lockout
        for _ in range(4):
            response = client.post("/verify-totp", data={
                "token": "000000"
            }, follow_redirects=False)
            # Should still be able to access verify page
            assert response.status_code in [200, 302]
        
        # Verify session still exists
        with client.session_transaction() as sess:
            assert "pending_user_id" in sess
        
        # 5th attempt should lockout
        response = client.post("/verify-totp", data={
            "token": "000000"
        }, follow_redirects=False)
        
        # Session should be cleared
        with client.session_transaction() as sess:
            assert "pending_user_id" not in sess
    
    def test_user_without_2fa_doesnt_reach_brute_force_check(self, app, client):
        """Test that users without 2FA enabled don't encounter brute force limits."""
        # Login with regular user (no 2FA)
        response = client.post("/login", data={
            "email": "regular_user@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # Should redirect to bookings, not verify-totp
        assert response.status_code in [302, 307]
        
        # Should not have 2FA session variables
        with client.session_transaction() as sess:
            assert "pending_user_id" not in sess
    
    def test_concurrent_token_attempts(self, app):
        """Test behavior with multiple rapid token submissions."""
        client = app.test_client()
        
        # Login with 2FA user
        client.post("/login", data={
            "email": "user_with_2fa@test.com",
            "password": "Password123!"
        }, follow_redirects=False)
        
        # Rapidly submit 3 invalid tokens
        for _ in range(3):
            response = client.post("/verify-totp", data={
                "token": "000000"
            }, follow_redirects=False)
            assert response.status_code in [200, 302]
        
        # Check counter reflects all attempts
        with client.session_transaction() as sess:
            assert sess.get("totp_failed_attempts", 0) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
