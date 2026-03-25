# -*- coding: utf-8 -*-
"""
Created on Tue Jan 13 14:08:29 2026

@author: NBoyd1
"""

import random
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from app.db import Base
from app.models import Site, Machine, User, Location
from app.security import hash_password

def seed(db_url: str = "sqlite:///app.db"):
    engine = create_engine(
        db_url,
        future=True,
        connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
    )
    Base.metadata.create_all(bind=engine)
    
    # Ensure 2FA columns exist (for existing databases)
    with engine.begin() as conn:
        if engine.dialect.name == "sqlite":
            # SQLite doesn't have IF NOT EXISTS for columns easily, check manually
            result = conn.execute(text("PRAGMA table_info(users)")).fetchall()
            column_names = [row[1] for row in result]
            if "two_fa_secret" not in column_names:
                conn.execute(text("ALTER TABLE users ADD COLUMN two_fa_secret VARCHAR(32) NULL"))
            if "two_fa_enabled" not in column_names:
                conn.execute(text("ALTER TABLE users ADD COLUMN two_fa_enabled BOOLEAN DEFAULT 0"))
        elif engine.dialect.name == "mssql":
            # SQL Server
            try:
                conn.execute(text("ALTER TABLE users ADD two_fa_secret VARCHAR(32) NULL"))
            except:
                pass
            try:
                conn.execute(text("ALTER TABLE users ADD two_fa_enabled BIT DEFAULT 0"))
            except:
                pass
    
    Session = sessionmaker(bind=engine, future=True)

    sites_data = [
        ("Test Hub North",    "MAN", "Manchester",   "England", "1 Piccadilly Gardens, Manchester, M1 1RG",  53.4808,  -2.2426),
        ("Test Hub South",    "LON", "London",       "England", "30 St Mary Axe, London, EC3A 8EP",           51.5072,  -0.1276),
        ("Test Hub Central",  "MKY", "Milton Keynes","England", "600 Silbury Blvd, Milton Keynes, MK9 3AT",  52.0406,  -0.7594),
        ("Test Hub West",     "BRS", "Bristol",      "England", "Temple Quay House, Bristol, BS1 6EG",        51.4545,  -2.5879),
        ("Test Hub Scotland", "EDI", "Edinburgh",    "Scotland","1 Waverley Bridge, Edinburgh, EH1 1BQ",      55.9533,  -3.1883),
    ]
    categories = ["Payments", "Devices", "Networking", "Core Platform", "Data Pipelines"]
    types = ["lab", "virtual"]

    with Session() as db:
        if db.execute(select(Site)).first():
            return  # already seeded

        sites = []
        for name, code, city, country, address, lat, lon in sites_data:
            s = Site(name=name, code=code, city=city, country=country, address=address, lat=lat, lon=lon)
            db.add(s)
            sites.append(s)
        db.flush()

        # Seed two standard locations per site: a lab area and a virtual lab
        # site_id → {"lab": location_id, "virtual": location_id}
        site_location_map: dict[int, dict[str, int]] = {}
        for site in sites:
            lab = Location(
                name=f"{site.city} Lab",
                code="LAB",
                site_id=site.id,
                floor="1",
                description="Physical lab area with test machines.",
            )
            virtual = Location(
                name=f"{site.city} Virtual Lab",
                code="VLAB",
                site_id=site.id,
                description="Virtual machines hosted at this site.",
            )
            db.add(lab)
            db.add(virtual)
            db.flush()
            site_location_map[site.id] = {"lab": lab.id, "virtual": virtual.id}
            # Example sub-location within the lab (hierarchy)
            db.add(Location(
                name=f"{site.city} Lab – Bay A",
                code="LAB-A",
                site_id=site.id,
                parent_id=lab.id,
                floor="1",
                description="Bay A – first row of test benches.",
            ))

        # 100 machines – each assigned to the appropriate location for its type
        for i in range(1, 101):
            mtype = random.choice(types)
            site = random.choice(sites)
            db.add(
                Machine(
                    name=f"TM-{i:03d}",
                    machine_type=mtype,
                    category=random.choice(categories),
                    status="available" if random.random() > 0.08 else "out_of_service",
                    site_id=site.id,
                    location_id=site_location_map[site.id][mtype],
                )
            )

        # demo users
        db.add_all(
            [
                User(
                    name="Admin User",
                    email="admin@example.com",
                    password_hash=hash_password("Admin123!"),
                    team="Operations",
                    role="admin",
                    status="active",
                    manager_email="director@example.com",
                ),
                User(
                    name="Approver User",
                    email="approver@example.com",
                    password_hash=hash_password("Approver123!"),
                    team="QA Governance",
                    role="approver",
                    status="active",
                    manager_email="director@example.com",
                ),
                User(
                    name="Standard User",
                    email="user@example.com",
                    password_hash=hash_password("User123!"),
                    team="Engineering",
                    role="user",
                    status="active",
                    manager_email="manager@example.com",
                ),
            ]
        )

        db.commit()

if __name__ == "__main__":
    seed()
    print("Seed complete.")
