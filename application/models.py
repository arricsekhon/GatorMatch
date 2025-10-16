from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, Text, func
from sqlalchemy.orm import validates
from werkzeug.security import check_password_hash, generate_password_hash
from flask_login import UserMixin

from .db import Base

class User(UserMixin, Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    # Flask-Login requires this to be str-convertible; default from UserMixin is fine.
    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @validates("email")
    def _normalize_email(self, key, value):
        return (value or "").strip().lower()

    @validates("name")
    def _normalize_name(self, key, value):
        return (value or "").strip()

# --- Tutor Applications ------------------------------------------------------

class TutorApplication(Base):
    __tablename__ = "tutor_applications"

    id = Column(Integer, primary_key=True)

    # Linked user
    user_id = Column(Integer, nullable=True)

    # Snapshot of identity at submission time
    name = Column(String(120), nullable=False)
    email = Column(String(255), nullable=False)

    # Profile details
    headline = Column(String(80), nullable=False)
    bio = Column(Text, nullable=False)

    # Comma-separated values for simple querying; keep JSON in availability_json
    meeting_options = Column(String(120), nullable=False)   # e.g. "library,zoom"
    courses_csv = Column(String(1000), nullable=False)      # e.g. "CSC 340|A; CSC 210|A-"
    availability_json = Column(Text, nullable=True)         # JSON map {"Mon-Morning": true, ...}

    # Uploaded documents (filenames only; storage path handled by app.py)
    documents_csv = Column(String(1200), nullable=True)     # "a.pdf,b.png"

    # Workflow
    status = Column(String(40), nullable=False, default="pending")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    def __repr__(self) -> str:
        return f"<TutorApplication id={self.id} email={self.email} status={self.status}>"