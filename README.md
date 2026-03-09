# AP-Assignment-1
Test Machine Booking Tool

## Description

The Test Machine Booking Tool is a Flask-based web application designed to manage and coordinate the booking of test machines across multiple sites. It provides a comprehensive solution for organizations that need to schedule and track the usage of lab and virtual test machines for various testing purposes. The application features user authentication, booking management, approval workflows, automated notifications, and administrative dashboards.

## Features

### Core Functionality
- **User Authentication & Authorization**: Secure login system with role-based access control (User, Approver, Admin)
- **Machine Booking System**: Create, view, and manage bookings for test machines
- **Multi-Site Support**: Manage test machines across multiple geographic locations (Manchester, London, Milton Keynes, Bristol, Edinburgh)
- **Approval Workflow**: Booking requests require approver review before being confirmed
- **Interactive Map View**: Visualize test machine locations across different sites

### Machine Management
- **Machine Categories**: Support for different machine categories (Payments, Devices, Networking, Core Platform, Data Pipelines)
- **Machine Types**: Manage both lab and virtual test machines
- **Availability Tracking**: Real-time status monitoring (available/out_of_service)
- **Conflict Detection**: Prevents double-booking of machines

### Advanced Features
- **Automated Notifications**: Background job system for sending booking updates and reminders
- **No-Show Detection**: Automatically marks bookings as no-shows when users don't check in
- **Check-in System**: Users can check in to their bookings when they arrive
- **Utilization Analytics**: Track machine usage and generate utilization reports
- **Audit Logging**: Complete audit trail of all actions performed in the system
- **Booking Validation**: Enforce booking rules and time windows

### Administrative Tools
- **Admin Dashboard**: Overview of bookings, utilization metrics, and system statistics
- **User Management**: Approve or reject user registrations
- **Booking Management**: Review, approve, or reject booking requests
- **Data Export**: Export booking data and audit logs to CSV
- **Machine Status Control**: Update machine availability and service status

## Getting Started

### Prerequisites
- Python 3.8 or higher
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

4. **Run the application**
   ```bash
   python run.py
   ```

   On first run, the application will automatically:
   - Create the database
   - Set up tables
   - Seed initial data (5 test sites, 100 test machines, 3 demo users)

5. **Access the application**
   
   Open your browser and navigate to: `http://127.0.0.1:5000`

### Demo Accounts

The application comes pre-seeded with the following demo accounts:

| Role | Email | Password |
|------|-------|----------|
| Admin | admin@example.com | Admin123! |
| Approver | approver@example.com | Approver123! |
| User | user@example.com | User123! |

### Running Tests

Execute the test suite with:
```bash
PYTHONPATH=. pytest
```

## Project Structure

```
AP-Assignment-1/
├── app/
│   ├── blueprints/       # Route handlers (auth, bookings, admin, map)
│   ├── services/         # Business logic (notifications, booking rules, utilization)
│   ├── templates/        # HTML templates
│   ├── static/           # CSS, JS, images
│   ├── models.py         # Database models
│   ├── forms.py          # WTForms definitions
│   └── __init__.py       # Application factory
├── tests/                # Test files
├── run.py               # Application entry point
├── seed.py              # Database seeding script
└── requirements.txt     # Python dependencies
```

## Technology Stack

- **Framework**: Flask 3.0.3
- **Database**: SQLAlchemy 2.0.31 (SQLite by default)
- **Authentication**: Flask-Login 0.6.3
- **Forms**: Flask-WTF 1.2.1, email-validator 2.1.1
- **Background Jobs**: APScheduler 3.10.4
- **Testing**: pytest 8.3.2
- **Configuration**: python-dotenv 1.0.1

## Control & Team Workflow

This project uses a structured Git workflow to support collaborative development and maintain code stability.

Branching Strategy
	•	The main branch is protected and acts as the integration branch.
	•	All development work is carried out in feature branches:
	•	feature/<description>
	•	fix/<description>
	•	chore/<description>

Pull Request Policy
	•	Direct commits to main are disabled.
	•	All changes must be merged via Pull Request.
	•	At least one peer approval is required before merging.
	•	All PR discussions must be resolved prior to integration.

Protection Controls
	•	Force pushes to main are disabled.
	•	Deletion of main is prevented.
	•	Merge history is preserved to maintain traceability.

Rationale

This workflow ensures:
	•	Code stability in the integration branch
	•	Peer-reviewed changes
	•	Clear traceability of feature development
	•	Professional collaboration aligned with industry best practice

## Governance & Risk Control Alignment

Because this system is designed to manage operational issues (e.g. bookings, faults, or service tasks), maintaining integrity in the integration branch is essential.

By enforcing pull-request based merging, peer review, and protected branch controls, we reduce the risk of:
	•	Introducing untested functionality into the operational workflow
	•	Regressions impacting issue tracking accuracy
	•	Loss of change history or traceability
	•	Single-point-of-failure development practices

This mirrors real-world governance models used in production IT service environments, where controlled promotion of changes is critical to maintaining system reliability and auditability.
