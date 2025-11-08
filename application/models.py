from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, Text, func, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import validates, relationship
from datetime import datetime
from werkzeug.security import check_password_hash, generate_password_hash
from flask_login import UserMixin

from .db import Base

import re
import unicodedata


class User(UserMixin, Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    # "user", "tutor", "admin"
    role = Column(String(20), nullable=False, default="student")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    applications = relationship(
        "TutorApplication",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    tutor_profile = relationship(
        "Tutor",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

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

    @property
    def is_admin(self) -> bool:
        return (self.role or "user") == "admin"

    @property
    def is_tutor(self) -> bool:
        return (self.role or "user") in ("tutor", "admin")

# --- Tutor Applications ------------------------------------------------------


class TutorApplication(Base):
    __tablename__ = "tutor_applications"

    id = Column(Integer, primary_key=True)

    # Link to users (FK)
    user_id = Column(Integer, ForeignKey(
        "users.id", ondelete="CASCADE"), nullable=False)

    # Snapshot of identity at submission time
    name = Column(String(120), nullable=False)
    email = Column(String(255), nullable=False)

    # Profile details
    headline = Column(String(80), nullable=False)
    bio = Column(Text, nullable=False)

    meeting_options = Column(String(120), nullable=False)   # "library,zoom"
    # "CSC 340|A; CSC 210|A-"
    courses_csv = Column(String(1000), nullable=False)
    availability_json = Column(Text, nullable=True)
    documents_csv = Column(String(1200), nullable=True)

    status = Column(String(40), nullable=False, default="pending")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    user = relationship("User", back_populates="applications")

    def __repr__(self) -> str:
        return f"<TutorApplication id={self.id} email={self.email} status={self.status}>"


# ----- Tutors ----------------------

class Tutor(Base):
    __tablename__ = "tutors"

    id = Column(Integer, primary_key=True)

    user_id = Column(Integer, ForeignKey(
        "users.id", ondelete="CASCADE"), nullable=False, unique=True)
    application_id = Column(Integer, ForeignKey(
        "tutor_applications.id", ondelete="SET NULL"), nullable=True, unique=True)

    # Public identity/SEO
    slug = Column(String(140), nullable=False, unique=True, index=True)
    headline = Column(String(80), nullable=False)
    bio = Column(Text, nullable=False)

    # Searchable facets (denormalized from the approved application)
    meeting_options = Column(String(120), nullable=False)     # "library,zoom"
    # "CSC 340; CSC 210"
    courses_csv = Column(String(1000), nullable=False)

    # Lifecycle
    is_active = Column(Integer, nullable=False, default=1)    # 1=true, 0=false
    published_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)

    # Cached UI metrics
    hours_total_min = Column(Integer, nullable=False, default=0)
    # store *10 if you want 4.5 (=45)
    rating_avg = Column(Integer, nullable=False, default=0)
    rating_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False,
                        server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="tutor_profile")
    application = relationship("TutorApplication")

    __table_args__ = (
        Index("ix_tutors_active_published", "is_active", "published_at"),
    )

# ------- Slug helper -----------


_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(text: str, extra: str = "") -> str:
    t = unicodedata.normalize("NFKD", text or "").encode(
        "ascii", "ignore").decode("ascii")
    t = _slug_re.sub("-", t.lower()).strip("-") or "tutor"
    if extra:
        t = f"{t}-{extra}"
    return t[:120]
