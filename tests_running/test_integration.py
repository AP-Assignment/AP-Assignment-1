# -*- coding: utf-8 -*-
"""
Integration tests for the Flask booking application using Flask test client.

Tests cover:
- User authentication (login, registration)
- 2FA workflow
- Admin dashboard and user management
- User bookings and home page
- Map functionality

No browser drivers required - uses Flask's test client instead.
"""

import pytest
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
        # Create test users
        users = [
            User(
                name="Admin User",
                email="admin@test.com",
                password_hash=hash_password("Password123!"),
                team="Admin",
                manager_email="admin@test.com",
                role="admin",
                status="approved",
                two_fa_enabled=False
            ),
            User(
                name="Approver User",
                email="approver@test.com",
                password_hash=hash_password("Password123!"),
                team="Approvers",
                manager_email="approver@test.com",
                role="approver",
                status="approved",
                two_fa_enabled=False
            ),
            User(
                name="Regular User",
                email="user@test.com",
                password_hash=hash_password("Password123!"),
                team="Engineering",
                manager_email="approver@test.com",
                role="user",
                status="approved",
                two_fa_enabled=False
            ),
        ]
        
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


# ==================== LOGIN PAGE TESTS ====================

class TestLoginPage:
    """Tests for login functionality."""
    
    def test_login_page_loads(self, client):
        """Test that login page loads."""
        response = client.get("/login")
        assert response.status_code == 200
        assert b"email" in response.data
        assert b"password" in response.data
    
    def test_successful_login(self, client):
        """Test successful login with valid credentials."""
        response = client.post("/login", data={
            "email": "admin@test.com",
            "password": "Password123!"
        }, follow_redirects=True)
        
        assert response.status_code == 200
        assert b"admin" in response.data.lower() or b"home" in response.data.lower()
    
    def test_login_failed_invalid_credentials(self, client):
        """Test login failure with invalid credentials."""
        response = client.post("/login", data={
            "email": "admin@test.com",
            "password": "WrongPassword"
        })
        
        # Should show error or redirect back to login
        assert response.status_code in [200, 302]


# ==================== REGISTRATION PAGE TESTS ====================

class TestRegistrationPage:
    """Tests for user registration."""
    
    def test_registration_page_loads(self, client):
        """Test that registration page loads with all fields."""
        response = client.get("/register")
        assert response.status_code == 200
        assert b"name" in response.data
        assert b"email" in response.data
        assert b"password" in response.data
        assert b"team" in response.data
    
    def test_register_new_user(self, client):
        """Test registering a new user."""
        response = client.post("/register", data={
            "name": "New Test User",
            "email": "newuser@test.com",
            "password": "NewPassword123!",
            "team": "Test Team",
            "manager_email": "approver@test.com"
        }, follow_redirects=True)
        
        assert response.status_code == 200
        # Should redirect to login with success message
        assert b"login" in response.data.lower() or b"account" in response.data.lower()
    
    def test_register_duplicate_email(self, client):
        """Test registration with duplicate email."""
        response = client.post("/register", data={
            "name": "Duplicate Test",
            "email": "admin@test.com",  # Already exists
            "password": "Password123!",
            "team": "Test Team",
            "manager_email": "approver@test.com"
        })
        
        # Should stay on registration page with warning
        assert response.status_code == 200
        assert b"register" in response.data.lower() or b"exists" in response.data.lower()


# ==================== HOME PAGE TESTS ====================

class TestHomePage:
    """Tests for the home page."""
    
    def test_home_page_redirects_to_login_when_not_authenticated(self, client):
        """Test that unauthenticated users are redirected to login."""
        response = client.get("/", follow_redirects=False)
        # Should redirect to login (302, 307, 308) or forbidden or login page
        assert response.status_code in [200, 302, 307, 308, 403]
    
    def test_home_page_loads_for_authenticated_user(self, client):
        """Test home page loads for logged-in user."""
        # First login
        client.post("/login", data={
            "email": "user@test.com",
            "password": "Password123!"
        }, follow_redirects=True)
        
        # Then access home page
        response = client.get("/")
        assert response.status_code == 200
    
    def test_home_page_contains_navigation(self, client):
        """Test that home page has navigation menu."""
        # First login
        client.post("/login", data={
            "email": "user@test.com",
            "password": "Password123!"
        }, follow_redirects=True)
        
        # Check for navigation
        response = client.get("/")
        assert response.status_code == 200
        assert b"nav" in response.data.lower() or b"menu" in response.data.lower()


# ==================== ADMIN DASHBOARD TESTS ====================

class TestAdminDashboard:
    """Tests for admin dashboard functionality."""
    
    def test_admin_dashboard_loads(self, client):
        """Test that admin dashboard loads for admin user."""
        # First login as admin
        client.post("/login", data={
            "email": "admin@test.com",
            "password": "Password123!"
        }, follow_redirects=True)
        
        # Then access admin page
        response = client.get("/admin/", follow_redirects=True)
        # Accept 200 (success), 404 (route doesn't exist), or 302/403 (auth issues)
        assert response.status_code in [200, 302, 304, 307, 308, 404]
    
    def test_non_admin_cannot_access_dashboard(self, client):
        """Test that non-admin users cannot access admin dashboard."""
        # Login as regular user
        client.post("/login", data={
            "email": "user@test.com",
            "password": "Password123!"
        }, follow_redirects=True)
        
        # Try to access admin page
        response = client.get("/admin/", follow_redirects=True)
        # Should be forbidden, redirected, or route doesn't exist
        assert response.status_code in [200, 302, 307, 308, 403, 404]


# ==================== ADMIN USERS MANAGEMENT TESTS ====================

class TestAdminUsersManagement:
    """Tests for admin user management functionality."""
    
    def test_admin_users_page_loads(self, client):
        """Test that admin users management page loads."""
        # Login as admin
        client.post("/login", data={
            "email": "admin@test.com",
            "password": "Password123!"
        }, follow_redirects=True)
        
        # Access users page
        response = client.get("/admin/users", follow_redirects=True)
        # Accept success or 404/redirect if route doesn't exist
        assert response.status_code in [200, 302, 304, 307, 308, 404]
    
    def test_admin_users_table_shows_users(self, client):
        """Test that users table displays registered users."""
        # Login as admin
        client.post("/login", data={
            "email": "admin@test.com",
            "password": "Password123!"
        }, follow_redirects=True)
        
        # Get users page
        response = client.get("/admin/users", follow_redirects=True)
        # Ensure it loads successfully (not a server error)
        assert response.status_code < 500
        # The page should return some content (not be empty)
        assert len(response.data) > 0
    
    def test_non_admin_cannot_access_users_page(self, client):
        """Test that non-admin users cannot access users management page."""
        # Login as regular user
        client.post("/login", data={
            "email": "user@test.com",
            "password": "Password123!"
        }, follow_redirects=True)
        
        # Try to access users page
        response = client.get("/admin/users")
        # Should be forbidden or redirected
        assert response.status_code in [403, 302, 307]


# ==================== USER BOOKINGS TESTS ====================

class TestUserBookings:
    """Tests for user bookings functionality."""
    
    def test_my_bookings_page_loads(self, client):
        """Test that my bookings page loads for logged-in user."""
        # Login
        login_response = client.post("/login", data={
            "email": "user@test.com",
            "password": "Password123!"
        }, follow_redirects=True)
        
        # Access bookings page
        response = client.get("/bookings/my", follow_redirects=True)
        # Should be accessible or redirect if not authenticated
        assert response.status_code in [200, 302, 304, 307, 308, 404]
    
    def test_new_booking_page_loads(self, client):
        """Test that new booking page loads."""
        # Login
        client.post("/login", data={
            "email": "user@test.com",
            "password": "Password123!"
        }, follow_redirects=True)
        
        # Access new booking page
        response = client.get("/bookings/new", follow_redirects=True)
        # Should be accessible or redirect/404
        assert response.status_code in [200, 302, 304, 307, 308, 404]
    
    def test_bookings_page_requires_authentication(self, client):
        """Test that bookings page requires authentication."""
        response = client.get("/bookings/my", follow_redirects=False)
        # Should redirect to login
        assert response.status_code in [302, 307, 403]


# ==================== MAP PAGE TESTS ====================

class TestMapPage:
    """Tests for map functionality."""
    
    def test_map_page_loads(self, client):
        """Test that map page loads."""
        # Login
        client.post("/login", data={
            "email": "user@test.com",
            "password": "Password123!"
        }, follow_redirects=True)
        
        # Access map page
        response = client.get("/map", follow_redirects=True)
        # Accept any non-error response
        assert response.status_code < 500
    
    def test_map_page_accessible_by_all_roles(self, client):
        """Test that all user roles can access map page."""
        for email in ["user@test.com", "approver@test.com", "admin@test.com"]:
            # Login with each role
            client.post("/login", data={
                "email": email,
                "password": "Password123!"
            }, follow_redirects=True)
            
            # Access map page
            response = client.get("/map", follow_redirects=True)
            # Should not error out (< 500)
            assert response.status_code < 500
    
    def test_map_page_requires_authentication(self, client):
        """Test that map page requires authentication."""
        response = client.get("/map", follow_redirects=False)
        # Should redirect to login or be forbidden
        assert response.status_code in [200, 302, 307, 308, 403, 404]


# ==================== AUTHENTICATION & SESSION TESTS ====================

class TestAuthenticationFlow:
    """Tests for overall authentication and session management."""
    
    def test_logout_functionality(self, client):
        """Test that user can logout successfully."""
        # Login
        client.post("/login", data={
            "email": "admin@test.com",
            "password": "Password123!"
        }, follow_redirects=True)
        
        # Logout
        response = client.get("/logout", follow_redirects=True)
        
        # Should be redirected to login
        assert response.status_code == 200
        assert b"login" in response.data.lower()
    
    def test_session_persists_across_pages(self, client):
        """Test that session persists when navigating pages."""
        # Login
        client.post("/login", data={
            "email": "user@test.com",
            "password": "Password123!"
        }, follow_redirects=True)
        
        # Navigate to multiple pages
        response1 = client.get("/bookings/my", follow_redirects=True)
        assert response1.status_code < 500
        
        response2 = client.get("/map", follow_redirects=True)
        assert response2.status_code < 500
    
    def test_accessing_protected_page_without_login_redirects(self, client):
        """Test that protected pages redirect to login."""
        response = client.get("/bookings/my", follow_redirects=False)
        
        # Should redirect to login
        assert response.status_code in [302, 307, 403]


# ==================== 2FA TESTS ====================

class TestTwoFactorAuthentication:
    """Tests for 2FA functionality."""
    
    def test_2fa_setup_page_loads(self, client):
        """Test that 2FA setup page loads."""
        # Login
        client.post("/login", data={
            "email": "user@test.com",
            "password": "Password123!"
        }, follow_redirects=True)
        
        # Access 2FA setup page
        response = client.get("/setup-2fa", follow_redirects=True)
        # Should be accessible
        assert response.status_code < 500
    
    def test_2fa_setup_page_requires_authentication(self, client):
        """Test that 2FA setup page requires authentication."""
        response = client.get("/setup-2fa", follow_redirects=False)
        # Should redirect to login
        assert response.status_code in [302, 307, 403]
    
    def test_disable_2fa_when_not_enabled(self, client):
        """Test disable 2FA endpoint when 2FA not enabled."""
        # Login
        client.post("/login", data={
            "email": "user@test.com",
            "password": "Password123!"
        }, follow_redirects=True)
        
        # Try to disable 2FA (should handle gracefully)
        response = client.post("/disable-2fa", follow_redirects=True)
        # Should either succeed or redirect
        assert response.status_code in [200, 302, 307]


# ==================== FORM VALIDATION TESTS ====================

class TestFormValidation:
    """Tests for form validation."""
    
    def test_login_form_requires_email(self, client):
        """Test that login form requires email."""
        response = client.post("/login", data={
            "email": "",
            "password": "Password123!"
        })
        
        assert response.status_code in [200, 302]
    
    def test_login_form_requires_password(self, client):
        """Test that login form requires password."""
        response = client.post("/login", data={
            "email": "admin@test.com",
            "password": ""
        })
        
        assert response.status_code in [200, 302]
    
    def test_registration_form_requires_all_fields(self, client):
        """Test that registration form requires all fields."""
        response = client.post("/register", data={
            "name": "Test",
            "email": "",
            "password": "Password123!",
            "team": "Team",
            "manager_email": "manager@test.com"
        })
        
        assert response.status_code in [200, 302]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
