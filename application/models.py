from __future__ import annotations

from datetime import datetime, time
import re
import unicodedata

from flask_login import UserMixin
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    Time,
    func,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import validates, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from .db import Base


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class User(UserMixin, Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    # "student", "tutor", "admin"
    role = Column(String(20), nullable=False, default="student")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    # Relationships
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

    # As a student in tutoring sessions
    student_sessions = relationship(
        "TutoringSession",
        back_populates="student",
        foreign_keys="TutoringSession.student_id",
        cascade="all, delete-orphan",
    )

    # Messaging
    messages_sent = relationship(
        "Message",
        back_populates="sender",
        foreign_keys="Message.sender_id",
        cascade="all, delete-orphan",
    )

    message_threads_as_student = relationship(
        "MessageThread",
        back_populates="student",
        foreign_keys="MessageThread.student_id",
        cascade="all, delete-orphan",
    )

    # Password helpers -------------------------------------------------------

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
        return (self.role or "student") == "admin"

    @property
    def is_tutor(self) -> bool:
        return (self.role or "student") in ("tutor", "admin")

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} role={self.role}>"


# ---------------------------------------------------------------------------
# Tutor Applications
# ---------------------------------------------------------------------------


class TutorApplication(Base):
    __tablename__ = "tutor_applications"

    id = Column(Integer, primary_key=True)

    # Link to users (FK)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Snapshot of identity at submission time
    name = Column(String(120), nullable=False)
    email = Column(String(255), nullable=False)

    # Profile details
    headline = Column(String(80), nullable=False)
    bio = Column(Text, nullable=False)

    meeting_options = Column(String(120), nullable=False)  # "library,zoom"
    # "CSC 340|A; CSC 210|A-"
    courses_csv = Column(String(1000), nullable=False)
    availability_json = Column(Text, nullable=True)
    documents_csv = Column(String(1200), nullable=True)

    status = Column(String(40), nullable=False, default="pending")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    user = relationship("User", back_populates="applications")

    def __repr__(self) -> str:
        return (
            f"<TutorApplication id={self.id} email={self.email} "
            f"status={self.status}>"
        )


# ---------------------------------------------------------------------------
# Tutors
# ---------------------------------------------------------------------------


class Tutor(Base):
    __tablename__ = "tutors"

    id = Column(Integer, primary_key=True)

    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    application_id = Column(
        Integer,
        ForeignKey("tutor_applications.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )

    # Public identity/SEO
    slug = Column(String(140), nullable=False, unique=True, index=True)
    headline = Column(String(80), nullable=False)
    bio = Column(Text, nullable=False)
    avatar_url = Column(String(500), nullable=True)  # Profile picture path

    # Searchable facets (denormalized from the approved application)
    meeting_options = Column(String(120), nullable=False)  # "library,zoom"
    # "CSC 340; CSC 210"
    courses_csv = Column(String(1000), nullable=False)

    # Lifecycle
    is_active = Column(Integer, nullable=False, default=1)  # 1=true, 0=false
    published_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)

    # Cached UI metrics
    hours_total_min = Column(Integer, nullable=False, default=0)
    # store *10 if you want 4.5 (=45)
    rating_avg = Column(Integer, nullable=False, default=0)
    rating_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user = relationship("User", back_populates="tutor_profile")
    application = relationship("TutorApplication")

    # Tutoring sessions (as the tutor)
    sessions = relationship(
        "TutoringSession",
        back_populates="tutor",
        cascade="all, delete-orphan",
    )

    # Availability blocks
    availability_blocks = relationship(
        "TutorAvailabilityBlock",
        back_populates="tutor",
        cascade="all, delete-orphan",
    )

    # Messaging threads
    message_threads = relationship(
        "MessageThread",
        back_populates="tutor",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_tutors_active_published", "is_active", "published_at"),
    )

    def __repr__(self) -> str:
        return f"<Tutor id={self.id} slug={self.slug} user_id={self.user_id}>"

    @property
    def courses_list(self) -> list[str]:
        """Convenience: split courses_csv into a list of codes."""
        txt = (self.courses_csv or "").strip()
        if not txt:
            return []
        # simple split on semicolon or comma
        parts = [p.strip() for p in re.split(r"[;,]", txt) if p.strip()]
        return parts


# ---------------------------------------------------------------------------
# Tutoring sessions
# ---------------------------------------------------------------------------


class TutoringSession(Base):
    """
    Backed by the `sessions` table.

    Drives:
    - Session Requests
    - Upcoming Sessions
    - Next Session
    - Session History
    - Your Students
    """

    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)

    tutor_id = Column(
        Integer,
        ForeignKey("tutors.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    course_code = Column(String(32), nullable=False)
    course_title = Column(String(255), nullable=False)

    location_type = Column(String(32), nullable=False)
    location_label = Column(String(255), nullable=False)

    start_at = Column(DateTime, nullable=False)
    end_at = Column(DateTime, nullable=False)

    # 'pending' | 'confirmed' | 'denied' | 'completed' | 'cancelled' | 'no_show'
    status = Column(String(32), nullable=False)
    # 'student' | 'tutor'
    requested_by = Column(String(16), nullable=False)

    meeting_url = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)  # Initial message from student

    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    approved_at = Column(DateTime, nullable=True)
    denied_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    rating_value = Column(Integer, nullable=True)
    rating_comment = Column(Text, nullable=True)

    tutor = relationship("Tutor", back_populates="sessions")
    student = relationship(
        "User",
        back_populates="student_sessions",
        foreign_keys=[student_id],
    )

    __table_args__ = (
        Index("ix_sessions_start_at", "start_at"),
        Index("ix_sessions_status", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<TutoringSession id={self.id} tutor_id={self.tutor_id} "
            f"student_id={self.student_id} status={self.status}>"
        )

    @property
    def is_upcoming(self) -> bool:
        return self.start_at and self.start_at > datetime.utcnow()

    @property
    def duration_minutes(self) -> int | None:
        if not self.start_at or not self.end_at:
            return None
        delta = self.end_at - self.start_at
        return int(delta.total_seconds() // 60)


# ---------------------------------------------------------------------------
# Tutor availability
# ---------------------------------------------------------------------------


class TutorAvailabilityBlock(Base):
    """
    Backed by `tutor_availability_blocks`.

    Represents a single block on a specific weekday for a tutor.
    """

    __tablename__ = "tutor_availability_blocks"

    id = Column(Integer, primary_key=True)

    tutor_id = Column(
        Integer,
        ForeignKey("tutors.id", ondelete="CASCADE"),
        nullable=False,
    )

    # 0=Monday .. 6=Sunday
    weekday = Column(Integer, nullable=False)

    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    # Optional CSVs
    meeting_options = Column(String(255), nullable=True)
    courses = Column(String(1000), nullable=True)

    # 1=true, 0=false
    is_active = Column(Integer, nullable=False, default=1)

    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    tutor = relationship("Tutor", back_populates="availability_blocks")

    def __repr__(self) -> str:
        return (
            f"<TutorAvailabilityBlock id={self.id} tutor_id={self.tutor_id} "
            f"weekday={self.weekday} {self.start_time}-{self.end_time}>"
        )

    @property
    def is_available(self) -> bool:
        return bool(self.is_active)

    @property
    def meeting_options_list(self) -> list[str]:
        txt = (self.meeting_options or "").strip()
        if not txt:
            return []
        return [p.strip() for p in txt.split(",") if p.strip()]

    @property
    def courses_list(self) -> list[str]:
        txt = (self.courses or "").strip()
        if not txt:
            return []
        return [p.strip() for p in re.split(r"[;,]", txt) if p.strip()]


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------


class MessageThread(Base):
    """
    Backed by `message_threads`.

    One thread per tutor–student subject line.
    """

    __tablename__ = "message_threads"

    id = Column(Integer, primary_key=True)

    tutor_id = Column(
        Integer,
        ForeignKey("tutors.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    subject = Column(String(255), nullable=False)

    # 'tutor' | 'student'
    started_by = Column(String(16), nullable=False)

    # from tutor’s point of view
    tutor_status = Column(String(32), nullable=False, default="awaiting_reply")
    student_status = Column(String(32), nullable=True)

    last_message_at = Column(DateTime, nullable=False, server_default=func.now())
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    tutor = relationship("Tutor", back_populates="message_threads")
    student = relationship(
        "User",
        back_populates="message_threads_as_student",
        foreign_keys=[student_id],
    )

    messages = relationship(
        "Message",
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="Message.sent_at",
    )

    def __repr__(self) -> str:
        return (
            f"<MessageThread id={self.id} tutor_id={self.tutor_id} "
            f"student_id={self.student_id} subject={self.subject!r}>"
        )


class Message(Base):
    """
    Backed by `messages`.
    """

    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)

    thread_id = Column(
        Integer,
        ForeignKey("message_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    body = Column(Text, nullable=False)
    sent_at = Column(DateTime, nullable=False, server_default=func.now())

    thread = relationship("MessageThread", back_populates="messages")
    sender = relationship("User", back_populates="messages_sent")

    def __repr__(self) -> str:
        return (
            f"<Message id={self.id} thread_id={self.thread_id} "
            f"sender_id={self.sender_id}>"
        )


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------

_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(text: str, extra: str = "") -> str:
    t = (
        unicodedata.normalize("NFKD", text or "")
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    t = _slug_re.sub("-", t.lower()).strip("-") or "tutor"
    if extra:
        t = f"{t}-{extra}"
    return t[:120]
