# AP-Assignment-1
Test Machine Booking

## Description

Test Machine Booking is a Flask-based web application for managing bookings of physical and virtual test machines across multiple sites. The final system supports user registration and approval, machine booking, access request workflows, assignment tracking, background automation, audit logging, utilisation reporting, and administrative oversight.

The application is designed to demonstrate a realistic operational workflow in which users request resources, approvers and admins review requests, and the system automatically monitors SLA and booking events in the background. The project also includes a cloud-hosted beta deployment to demonstrate online access beyond local desktop use.

## Features

### Core Functionality
- **User Authentication & Authorisation**: Secure login with role-based access control (`user`, `approver`, `admin`)
- **User Registration Workflow**: New users can register for an account, which must be approved before sign-in
- **Two-Factor Authentication (2FA)**: Optional TOTP-based 2FA can be enabled per user account for additional login security
- **Machine Booking System**: Create, view, and manage bookings for one or more test machines
- **Multi-Site Support**: Manage machines across multiple locations including Manchester, London, Milton Keynes, Bristol, and Edinburgh
- **Interactive Map View**: Visualise site locations geographically

### Booking and Access Management
- **Booking Approval Workflow**: Booking requests are reviewed by approvers/admins before becoming active
- **Conflict Detection**: Prevents approval of bookings that overlap with existing approved bookings
- **Check-in System**: Users can check in to approved bookings
- **Access Request Workflow**: Site access requests can be linked to bookings and reviewed separately
- **Assignment Management**: Access requests can also be associated with formal assignments/projects
- **Approval Ordering Rules**: Linked access requests can only be approved after the associated booking is approved
- **Evidence Support**: Supporting evidence can be linked to access requests and/or assignments

### Automation and Monitoring
- **Automated Notifications**: Background processing sends queued user notifications
- **SLA Monitoring**: Pending booking requests and access requests are monitored against warning, breach, and expiry thresholds
- **No-Show Detection**: Approved bookings are automatically marked as no-shows if users fail to check in within the grace period
- **Booking Window Monitoring**: Detects upcoming booking windows and missed check-ins
- **Audit Logging**: Records key user and system actions for traceability
- **Utilisation Reporting**: Tracks machine usage and supports operational reporting

### Administrative Tools
- **Admin Dashboard**: View booking, access request, utilisation, and SLA statistics
- **User Management**: Approve or reject pending registrations
- **Booking Management**: Approve or reject booking requests
- **Access Request Management**: Approve or reject access requests
- **Machine Status Control**: Update machine availability and service status
- **Data Export**: Export selected operational data to CSV

## Getting Started

### Prerequisites
- Python 3.12 recommended
- pip (Python package installer)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/AP-Assignment/AP-Assignment-1.git
   cd AP-Assignment-1
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up environment variables** (optional)

   Create a `.env` file in the root directory:
   ```
   SECRET_KEY=your-secret-key-here
   DATABASE_URL=sqlite:///app.db
   ```

   Notes:
   - `DATABASE_URL` defaults to `sqlite:///app.db` if not provided
   - `CONNECTION_STRING` can also be used as an alternative database environment variable
   - SQLite is the default local development database

4. **Run the application**
   ```bash
   python run.py
   ```

   On first local run with a new SQLite database, the application will automatically:
   - Create the database
   - Create the tables
   - Seed initial demo data

5. **Access the application**

   Open your browser and navigate to: `http://127.0.0.1:5000`

### Default Accounts

When the demo seed data is loaded, the following accounts are available:

| Role | Email | Password |
|------|-------|----------|
| Admin | admin@example.com | Admin123! |
| Approver | approver@example.com | Approver123! |
| User | user@example.com | User123! |

### Registration and Login Notes

- New users can register through the UI
- Newly registered users are created with `pending` status
- Pending users cannot sign in until approved by an admin
- Users can optionally enable 2FA for account protection after logging in
- The seeded admin account is intended to support first-time testing of the approval workflow

### Running Tests

Execute the test suite with:
```bash
PYTHONPATH=. pytest
```

## Project Structure

```text
AP-Assignment-1/
├── app/
│   ├── automation/       # Background jobs, rules, and automation actions
│   ├── blueprints/       # Route handlers (auth, bookings, admin, map)
│   ├── services/         # Business logic (notifications, booking rules, utilisation, evidence)
│   ├── templates/        # HTML templates
│   ├── static/           # CSS, JS, images
│   ├── models.py         # Database models
│   ├── forms.py          # WTForms definitions
│   └── __init__.py       # Application factory
├── docs/                 # Supporting design and automation documentation
├── migrations/           # Schema migration scripts
├── scripts/              # Helper scripts
├── tests/                # Automated tests
├── run.py                # Local application entry point
├── seed.py               # Demo data seeding script
└── requirements.txt      # Python dependencies
```

## Technology Stack

- **Framework**: Flask 3.0.3
- **Database Layer**: SQLAlchemy 2.0.31
- **Authentication**: Flask-Login 0.6.3
- **Forms**: Flask-WTF 1.2.1, email-validator 2.1.1
- **Background Jobs**: APScheduler 3.10.4
- **WSGI Server**: Gunicorn 21.2.0
- **Configuration**: python-dotenv 1.0.1, python-decouple
- **Security / 2FA**: pyotp 2.9.0, qrcode 7.4.2, Pillow 11.1.0
- **Additional Database Support**: `pymssql`
- **Testing**: pytest 8.3.2

## Deployment Notes

- Local development is designed around SQLite
- `run.py` bootstraps and seeds a new local SQLite database automatically
- In hosted environments, database creation may occur through the application factory, but demo seeding should not be assumed unless explicitly configured
- For production-style deployment, Gunicorn should load the Flask app object exposed by the project entrypoint

### Cloud Beta

In addition to the local development version, a cloud-hosted beta of the system has been produced to demonstrate deployment beyond a desktop environment. This version is described as a beta because some bugs and deployment issues remain under investigation, so it should not be treated as a fully production-ready release. Its inclusion is intended to show how the application can be adapted for remote access, environment-based configuration, and browser-based demonstration, while also reflecting the real-world challenges involved in moving a database-driven workflow system into the cloud.

Cloud beta URL:
- [https://ap-assignment-1-cloud.onrender.com/](https://ap-assignment-1-cloud.onrender.com/)

## Control & Team Workflow

This project uses a structured Git workflow to support collaborative development and maintain code stability.

### Branching Strategy
- The `main` branch is protected and acts as the integration branch
- Development work is carried out in feature branches such as:
- `feature/<description>`
- `fix/<description>`
- `chore/<description>`

### Pull Request Policy
- Direct commits to `main` are disabled
- All changes must be merged via Pull Request
- At least one peer approval is required before merging
- All PR discussions must be resolved before integration

### Protection Controls
- Force pushes to `main` are disabled
- Deletion of `main` is prevented
- Merge history is preserved to maintain traceability

### Rationale
This workflow helps ensure:
- code stability in the integration branch
- peer-reviewed changes
- clear traceability of feature development
- professional collaboration aligned with industry best practice

## Governance & Risk Control Alignment

Because this system is intended to manage operational activities such as machine bookings, approvals, and access requests, integrity and traceability are essential.

By enforcing pull-request-based merging, peer review, and protected branch controls, the project reduces the risk of:
- introducing untested functionality into operational workflows
- regressions affecting booking and approval accuracy
- loss of change history or auditability
- single-point-of-failure development practices

This reflects real-world governance practices used in production IT and service environments, where controlled change promotion is necessary to maintain reliability, accountability, and audit readiness.
