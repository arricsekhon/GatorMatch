# application/app.py
from __future__ import annotations

import os
import json
import logging
import re
from pathlib import Path
from functools import lru_cache, wraps
from collections import Counter, OrderedDict
from math import ceil
from typing import Iterable
from datetime import datetime, time

from dotenv import load_dotenv
from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    abort,
    redirect,
    url_for,
    flash,
)
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from flask_wtf import CSRFProtect
from sqlalchemy.orm import joinedload
from werkzeug.utils import secure_filename

from application.forms import LoginForm, SignupForm, TutorApplicationForm
from application.models import (
    User,
    TutorApplication,
    Tutor,
    TutoringSession,
    MessageThread,
    Message,
    TutorAvailabilityBlock,
)
Session = TutoringSession
from application.db import Base, engine, SessionLocal
from application.admin import bp as admin_bp
# --- IMPORT THE NEW JITSI HELPER ---
from application.jitshi_helper import create_jitsi_link 

load_dotenv()

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
DATA_FILE = BASE_DIR / "data" / "team1_section4.json"
# will be created on first use
UPLOAD_DIR = STATIC_DIR / "uploads"

app = Flask(
    __name__,
    template_folder=str(TEMPLATE_DIR),
    static_folder=str(STATIC_DIR),
)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-key")
app.register_blueprint(admin_bp)

# Security-focused cookie defaults
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "1") == "1",
)

# Upload policy (PDF/PNG/JPG, up to 10 MB each, max 3 files)
app.config.setdefault("MAX_CONTENT_LENGTH", 30 * 1024 * 1024)  # 30 MB
ALLOWED_EXTS = {".pdf", ".png", ".jpg", ".jpeg"}

app.logger.setLevel(logging.INFO)
log = app.logger

csrf = CSRFProtect(app)

# -------------------- DB wiring --------------------

Base.metadata.create_all(bind=engine)

login_manager = LoginManager(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    with SessionLocal() as db:
        try:
            return db.get(User, int(user_id))
        except Exception:
            return None


# -------------------- Data loading & caching --------------------


@lru_cache(maxsize=1)
def _load_cached(mtime: int):
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_team_data():
    try:
        mtime = int(DATA_FILE.stat().st_mtime)
        return _load_cached(mtime)
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError):
        abort(500)


def find_member(slug: str):
    members = load_team_data()
    return next((m for m in members if m.get("slug") == slug), None)


# -------------------- Normalization & helpers --------------------

COURSE_RE = re.compile(r"\b([A-Z]{2,4})\s?(\d{3})\b", re.IGNORECASE)


def normalize_tutor(m: dict) -> dict:
    name = (m.get("name") or "").strip()
    first = name.split()[0] if name else "Tutor"
    avatar = m.get("avatar") or ""
    rating = float(m.get("rating_avg", 0) or 0.0)
    rating_count = int(m.get("rating_count", 0) or 0)
    hours = int(m.get("hours_total", 0) or 0)
    courses = list(m.get("courses", []))
    locations = list(m.get("locations", []))

    probe = " ".join(
        [
            m.get("bio", ""),
            m.get("role", ""),
            m.get("subtitle", ""),
            " ".join(s for s in m.get("skills", []) if isinstance(s, str)),
            " ".join(
                c.get("title", "")
                for c in m.get("contributions", [])
                if isinstance(c, dict)
            ),
        ]
    )
    for dept, num in COURSE_RE.findall(probe):
        courses.append(f"{dept.upper()} {num}")

    seen = set()
    courses = [c for c in courses if not (c in seen or seen.add(c))]

    return {
        "slug": m.get("slug"),
        "name": name,
        "first": first,
        "avatar": avatar,
        "headline": m.get("role", ""),
        "desc": m.get("bio", ""),
        "rating": rating,
        "rating_count": rating_count,
        "hours": hours,
        "courses": courses,
        "locations": locations,
    }


def filter_sort_paginate(tutors: list[dict], args) -> dict:
    q = (args.get("q") or "").strip().casefold()
    subject = (args.get("subject") or "").strip().casefold()
    course = (args.get("course") or "").strip()
    location = (args.get("location") or "").strip().casefold()
    sort = (args.get("sort") or "best").strip()

    try:
        page = int(args.get("page", 1) or 1)
    except ValueError:
        page = 1
    page = max(page, 1)
    per_page = 5

    def matches(t: dict) -> bool:
        text = f"{t['name']} {t['headline']} {t['desc']}".casefold()
        if q and q not in text:
            return False
        if subject and not any(
            subject == c.split()[0].casefold() for c in t["courses"]
        ):
            return False
        if course and course not in t["courses"]:
            return False
        if location and not any(
            location == loc.casefold() for loc in t["locations"]
        ):
            return False
        return True

    filtered = [t for t in tutors if matches(t)]

    if sort == "highest":
        filtered.sort(
            key=lambda t: (t["rating"], t["rating_count"]),
            reverse=True,
        )
    elif sort == "lowest":
        filtered.sort(key=lambda t: (t["rating"], -t["rating_count"]))
    elif sort == "experience":
        filtered.sort(key=lambda t: t["hours"], reverse=True)
    else:

        def score(t: dict):
            name_hit = 1 if (q and q in t["name"].casefold()) else 0
            return (name_hit, t["rating"], t["rating_count"], t["hours"])

        filtered.sort(key=score, reverse=True)

    total = len(filtered)
    pages = max(ceil(total / per_page), 1)
    page = min(page, pages)
    start = (page - 1) * per_page
    page_items = filtered[start : start + per_page]

    subject_counts = Counter(
        c.split()[0] for t in filtered for c in t["courses"]
    )
    course_counts = Counter(c for t in filtered for c in t["courses"])
    location_counts = Counter(l for t in filtered for l in t["locations"])

    facets = {
        "subjects": OrderedDict(sorted(subject_counts.items())),
        "courses": OrderedDict(sorted(course_counts.items())),
        "locations": OrderedDict(sorted(location_counts.items())),
    }

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "pages": pages,
        "facets": facets,
    }


def ensure_dir(p: Path) -> None:
    """Create a directory if missing (no-op if it exists)."""
    p.mkdir(parents=True, exist_ok=True)


def save_files(files: Iterable, dest_dir: Path) -> list[str]:
    """
    Save up to 3 files with allowed extensions; return saved relative paths (under static/).
    Filenames are sanitized and de-duplicated.
    """
    ensure_dir(dest_dir)
    saved: list[str] = []

    for f in files:
        if not f:
            continue
        name = (f.filename or "").strip()
        if not name:
            continue
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_EXTS:
            # Skip silently; form validators already enforce types for normal submits.
            continue

        # Ensure unique, safe filename
        base = secure_filename(Path(name).stem) or "file"
        counter = 0
        while True:
            suffix = f"-{counter}" if counter else ""
            filename = f"{base}{suffix}{ext}"
            target = dest_dir / filename
            if not target.exists():
                break
            counter += 1

        f.save(str(target))
        # Return path relative to static/ so it can be referenced by url_for('static', filename=…)
        rel = str(target.relative_to(STATIC_DIR).as_posix())
        saved.append(rel)

        if len(saved) >= 3:
            break

    return saved


# -------------------- Context (GA hook) --------------------


@app.context_processor
def inject_globals():
    return {"GA_ID": os.getenv("GA_ID", "").strip()}


# -------------------- Routes: public pages --------------------


@app.route("/")
def home_page():
    return render_template("home.html")


@app.route("/about")
def team_page():
    members = load_team_data()
    return render_template("team.html", members=members)


@app.route("/about/<slug>")
def member_page(slug: str):
    person = find_member(slug)
    if not person:
        log.warning("Member not found: %s", slug)
        abort(404)

    person.setdefault("subtitle", "")
    person.setdefault("bio", "")
    person.setdefault("skills", [])
    person.setdefault("contributions", [])
    person.setdefault("github", "")
    person.setdefault("linkedin", "")
    person.setdefault("email", "")

    return render_template("member.html", person=person)


@app.route("/search")
def search_page():
    return render_template("search.html")


@app.route("/tutors/<slug>")
def tutor_profile(slug: str):
    """Display individual tutor profile from database."""
    with SessionLocal() as db:
        tutor = (
            db.query(Tutor)
            .options(joinedload(Tutor.user))
            .filter(Tutor.slug == slug)
            .filter(Tutor.is_active == 1)
            .filter(Tutor.published_at.isnot(None))
            .first()
        )

        if not tutor:
            log.warning("Tutor not found: %s", slug)
            abort(404)

        # Parse courses and meeting options
        courses = [
            c.strip()
            for c in (tutor.courses_csv or "").split(";")
            if c.strip()
        ]
        meeting_options = [
            m.strip()
            for m in (tutor.meeting_options or "").split(",")
            if m.strip()
        ]

        # Get availability blocks for this tutor
        availability_blocks = (
            db.query(TutorAvailabilityBlock)
            .filter(TutorAvailabilityBlock.tutor_id == tutor.id)
            .filter(TutorAvailabilityBlock.is_active == 1)
            .order_by(
                TutorAvailabilityBlock.weekday.asc(),
                TutorAvailabilityBlock.start_time.asc(),
            )
            .all()
        )

        # Get today's date for the form
        today = datetime.now().strftime("%Y-%m-%d")

        return render_template(
            "tutor_profile.html",
            tutor=tutor,
            courses=courses,
            meeting_options=meeting_options,
            availability_blocks=availability_blocks,
            today=today,
        )


@app.route("/tutors/<tutor_slug>/request", methods=["POST"])
@login_required
def request_session(tutor_slug: str):
    """Handle session request from student to tutor."""
    with SessionLocal() as db:
        # Find the tutor
        tutor = (
            db.query(Tutor)
            .options(joinedload(Tutor.user))
            .filter(Tutor.slug == tutor_slug)
            .filter(Tutor.is_active == 1)
            .first()
        )

        if not tutor:
            abort(404)

        # Get form data
        course = (request.form.get("course") or "").strip()
        date_str = (request.form.get("date") or "").strip()
        start_time_str = (request.form.get("start_time") or "").strip()
        end_time_str = (request.form.get("end_time") or "").strip()
        location = (request.form.get("location") or "").strip()
        message = (request.form.get("message") or "").strip()

        # Validate required fields
        if not all([course, date_str, start_time_str, end_time_str, location]):
            flash("Please fill in all required fields.", "danger")
            return redirect(url_for("tutor_profile", slug=tutor_slug) + "#contact")

        # Parse date and times
        try:
            session_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            start_time = datetime.strptime(start_time_str, "%H:%M").time()
            end_time = datetime.strptime(end_time_str, "%H:%M").time()
            
            start_at = datetime.combine(session_date, start_time)
            end_at = datetime.combine(session_date, end_time)
        except ValueError:
            flash("Invalid date or time format.", "danger")
            return redirect(url_for("tutor_profile", slug=tutor_slug) + "#contact")

        # Validate times
        if end_at <= start_at:
            flash("End time must be after start time.", "danger")
            return redirect(url_for("tutor_profile", slug=tutor_slug) + "#contact")

        # Create the session request
        session_request = Session(
            tutor_id=tutor.id,
            student_id=current_user.id,
            course_code=course.split()[0] if " " in course else course,
            course_title=course,
            location_type=location.lower().replace("-", "_").replace(" ", "_"),
            location_label=location,
            start_at=start_at,
            end_at=end_at,
            status="pending",
            requested_by="student",
            notes=message if message else None,
        )
        db.add(session_request)
        db.commit()

        flash("Session request sent! The tutor will review your request.", "success")
        return redirect(url_for("tutor_profile", slug=tutor_slug, requested=1) + "#contact")


@app.route("/tutor/sessions/<int:session_id>/approve", methods=["POST"])
@login_required
def approve_session(session_id: int):
    """Tutor approves a session request."""
    with SessionLocal() as db:
        # Get the session
        session_obj = db.query(Session).filter(Session.id == session_id).first()
        
        if not session_obj:
            flash("Session not found.", "danger")
            return redirect(url_for("tutor_dashboard"))
        
        # Verify the current user is the tutor for this session
        tutor = db.query(Tutor).filter(Tutor.user_id == current_user.id).first()
        if not tutor or session_obj.tutor_id != tutor.id:
            flash("You don't have permission to approve this session.", "danger")
            return redirect(url_for("tutor_dashboard"))
        
        # --- JITSI INTEGRATION ---
        # If location is 'jitsi' (or similar), generate link
        loc = (session_obj.location_type or "").lower()
        if "jitsi" in loc:
            # Generate the link using the course title as the topic
            link = create_jitsi_link(session_obj.course_title)
            session_obj.meeting_url = link
            flash(f"Session approved and Jitsi meeting room created!", "success")
        else:
            flash("Session approved! The student will be notified.", "success")
        
        # Approve the session
        session_obj.status = "approved"
        # If you have an approved_at column in your model, you can set it:
        session_obj.approved_at = datetime.utcnow()
        db.commit()
        
        return redirect(url_for("tutor_dashboard"))


@app.route("/tutor/sessions/<int:session_id>/deny", methods=["POST"])
@login_required
def deny_session(session_id: int):
    """Tutor denies a session request."""
    with SessionLocal() as db:
        # Get the session
        session_obj = db.query(Session).filter(Session.id == session_id).first()
        
        if not session_obj:
            flash("Session not found.", "danger")
            return redirect(url_for("tutor_dashboard"))
        
        # Verify the current user is the tutor for this session
        tutor = db.query(Tutor).filter(Tutor.user_id == current_user.id).first()
        if not tutor or session_obj.tutor_id != tutor.id:
            flash("You don't have permission to deny this session.", "danger")
            return redirect(url_for("tutor_dashboard"))
        
        # Deny the session
        session_obj.status = "denied"
        session_obj.denied_at = datetime.utcnow()
        db.commit()
        
        flash("Session request denied.", "info")
        return redirect(url_for("tutor_dashboard"))


@app.route("/sessions/<int:session_id>/cancel", methods=["POST"])
@login_required
def cancel_session(session_id: int):
    """Cancel a session - works for both students and tutors."""
    with SessionLocal() as db:
        # Get the session
        session_obj = db.query(Session).filter(Session.id == session_id).first()
        
        if not session_obj:
            flash("Session not found.", "danger")
            return redirect(url_for("student_dashboard"))
        
        # Check if user is the student OR the tutor for this session
        is_student = session_obj.student_id == current_user.id
        tutor = db.query(Tutor).filter(Tutor.user_id == current_user.id).first()
        is_tutor = tutor and session_obj.tutor_id == tutor.id
        
        if not is_student and not is_tutor:
            flash("You don't have permission to cancel this session.", "danger")
            return redirect(url_for("student_dashboard"))
        
        # Cancel the session
        session_obj.status = "cancelled"
        session_obj.cancelled_at = datetime.utcnow()
        db.commit()
        
        flash("Session cancelled successfully.", "info")
        
        # Redirect back to appropriate dashboard
        if is_tutor:
            return redirect(url_for("tutor_dashboard"))
        return redirect(url_for("student_dashboard"))


# -------------------- Messaging --------------------

@app.route("/messages")
@login_required
def messages_list():
    """View all message threads for the current user."""
    with SessionLocal() as db:
        # Check if user is a tutor
        tutor = db.query(Tutor).filter(Tutor.user_id == current_user.id).first()
        
        if tutor:
            # Tutor sees threads where they're the tutor
            threads = (
                db.query(MessageThread)
                .options(
                    joinedload(MessageThread.messages),
                    joinedload(MessageThread.student),
                )
                .filter(MessageThread.tutor_id == tutor.id)
                .order_by(MessageThread.last_message_at.desc())
                .all()
            )
        else:
            # Student sees threads where they're the student
            threads = (
                db.query(MessageThread)
                .options(
                    joinedload(MessageThread.messages),
                    joinedload(MessageThread.tutor).joinedload(Tutor.user),
                )
                .filter(MessageThread.student_id == current_user.id)
                .order_by(MessageThread.last_message_at.desc())
                .all()
            )
        
        return render_template("messages.html", threads=threads, is_tutor=bool(tutor))


@app.route("/messages/<int:thread_id>")
@login_required  
def view_thread(thread_id: int):
    """View a specific message thread."""
    with SessionLocal() as db:
        thread = (
            db.query(MessageThread)
            .options(
                joinedload(MessageThread.messages).joinedload(Message.sender),
                joinedload(MessageThread.tutor).joinedload(Tutor.user),
                joinedload(MessageThread.student),
            )
            .filter(MessageThread.id == thread_id)
            .first()
        )
        
        if not thread:
            abort(404)
        
        # Check permission - must be tutor or student in thread
        tutor = db.query(Tutor).filter(Tutor.user_id == current_user.id).first()
        is_tutor = tutor and thread.tutor_id == tutor.id
        is_student = thread.student_id == current_user.id
        
        if not is_tutor and not is_student:
            abort(403)
        
        # Fetch all threads for sidebar
        if is_tutor:
            all_threads = (
                db.query(MessageThread)
                .options(
                    joinedload(MessageThread.student),
                )
                .filter(MessageThread.tutor_id == tutor.id)
                .order_by(MessageThread.last_message_at.desc())
                .all()
            )
        else:
            all_threads = (
                db.query(MessageThread)
                .options(
                    joinedload(MessageThread.tutor).joinedload(Tutor.user),
                )
                .filter(MessageThread.student_id == current_user.id)
                .order_by(MessageThread.last_message_at.desc())
                .all()
            )
        
        return render_template("thread.html", thread=thread, is_tutor=is_tutor, all_threads=all_threads)


@app.route("/messages/<int:thread_id>/reply", methods=["POST"])
@login_required
def reply_to_thread(thread_id: int):
    """Reply to a message thread."""
    with SessionLocal() as db:
        thread = db.query(MessageThread).filter(MessageThread.id == thread_id).first()
        
        if not thread:
            flash("Thread not found.", "danger")
            return redirect(url_for("messages_list"))
        
        # Check permission
        tutor = db.query(Tutor).filter(Tutor.user_id == current_user.id).first()
        is_tutor = tutor and thread.tutor_id == tutor.id
        is_student = thread.student_id == current_user.id
        
        if not is_tutor and not is_student:
            flash("You don't have permission to reply to this thread.", "danger")
            return redirect(url_for("messages_list"))
        
        body = (request.form.get("body") or "").strip()
        if not body:
            flash("Message cannot be empty.", "danger")
            return redirect(url_for("view_thread", thread_id=thread_id))
        
        # Create the message
        msg = Message(
            thread_id=thread_id,
            sender_id=current_user.id,
            body=body,
        )
        db.add(msg)
        
        # Update thread's last_message_at
        thread.last_message_at = datetime.utcnow()
        
        db.commit()
        
        flash("Message sent!", "success")
        return redirect(url_for("view_thread", thread_id=thread_id))


@app.route("/messages/new/<int:tutor_id>", methods=["GET", "POST"])
@login_required
def new_message(tutor_id: int):
    """Start a new message thread with a tutor (student initiates)."""
    with SessionLocal() as db:
        tutor = (
            db.query(Tutor)
            .options(joinedload(Tutor.user))
            .filter(Tutor.id == tutor_id)
            .first()
        )
        
        if not tutor:
            abort(404)
        
        if request.method == "POST":
            subject = (request.form.get("subject") or "").strip()
            body = (request.form.get("body") or "").strip()
            
            if not subject or not body:
                flash("Subject and message are required.", "danger")
                return render_template("new_message.html", tutor=tutor)
            
            # Create thread
            thread = MessageThread(
                tutor_id=tutor.id,
                student_id=current_user.id,
                subject=subject,
                started_by="student",
                last_message_at=datetime.utcnow(),
            )
            db.add(thread)
            db.flush()  # Get the thread ID
            
            # Create first message
            msg = Message(
                thread_id=thread.id,
                sender_id=current_user.id,
                body=body,
            )
            db.add(msg)
            db.commit()
            
            flash("Message sent!", "success")
            return redirect(url_for("view_thread", thread_id=thread.id))
        
        return render_template("new_message.html", tutor=tutor)


@app.route("/messages/new/student/<int:student_id>", methods=["GET", "POST"])
@login_required
def new_message_to_student(student_id: int):
    """Start a new message thread with a student (tutor initiates)."""
    with SessionLocal() as db:
        # Verify current user is a tutor
        tutor = db.query(Tutor).filter(Tutor.user_id == current_user.id).first()
        if not tutor:
            flash("Only tutors can initiate messages to students.", "danger")
            return redirect(url_for("tutor_dashboard"))
        
        student = db.query(User).filter(User.id == student_id).first()
        
        if not student:
            abort(404)
        
        if request.method == "POST":
            subject = (request.form.get("subject") or "").strip()
            body = (request.form.get("body") or "").strip()
            
            if not subject or not body:
                flash("Subject and message are required.", "danger")
                return render_template("new_message_to_student.html", student=student)
            
            # Create thread
            thread = MessageThread(
                tutor_id=tutor.id,
                student_id=student.id,
                subject=subject,
                started_by="tutor",
                last_message_at=datetime.utcnow(),
            )
            db.add(thread)
            db.flush()  # Get the thread ID
            
            # Create first message
            msg = Message(
                thread_id=thread.id,
                sender_id=current_user.id,
                body=body,
            )
            db.add(msg)
            db.commit()
            
            flash("Message sent!", "success")
            return redirect(url_for("view_thread", thread_id=thread.id))
        
        return render_template("new_message_to_student.html", student=student)


@app.route("/results")
def results_page():
    q = (request.args.get("q") or "").strip().casefold()
    subject = (request.args.get("subject") or "").strip().upper()  # "CSC"
    course = (request.args.get("course") or "").strip().upper()  # "CSC 340"
    location = (request.args.get("location") or "").strip().casefold()
    sort = (request.args.get("sort") or "best").strip()

    with SessionLocal() as db:
        rows = (
            db.query(Tutor)
            .options(joinedload(Tutor.user))
            .filter(Tutor.is_active == 1)
            .filter(Tutor.published_at.isnot(None))
            .order_by(Tutor.created_at.desc())
            .all()
        )

    def matches(t: Tutor) -> bool:
        blob = (
            f"{t.slug} {t.headline} {t.bio} {t.courses_csv} {t.meeting_options}"
        ).casefold()
        if q and q not in blob:
            return False
        if subject:
            # subject matches prefix of a course code
            course_subjects = [
                c.split()[0]
                for c in (t.courses_csv or "").split(";")
                if c.strip()
            ]
            if subject not in " ".join(course_subjects).upper():
                return False
        if course and course not in (t.courses_csv or ""):
            return False
        if location and location not in (t.meeting_options or "").casefold():
            return False
        return True

    tutors = [t for t in rows if matches(t)]

    # sort options
    if sort == "highest":
        tutors.sort(
            key=lambda t: (t.rating_avg, t.rating_count),
            reverse=True,
        )
    elif sort == "lowest":
        tutors.sort(key=lambda t: (t.rating_avg, -t.rating_count))
    elif sort == "experience":
        tutors.sort(key=lambda t: t.hours_total_min, reverse=True)
    else:

        def score(t: Tutor):
            name_hit = 1 if (q and q in (t.slug or "").casefold()) else 0
            return (name_hit, t.rating_avg, t.rating_count, t.hours_total_min)

        tutors.sort(key=score, reverse=True)

    try:
        page = int(request.args.get("page", 1) or 1)
    except ValueError:
        page = 1
    page = max(page, 1)
    per_page = 5
    total = len(tutors)
    pages = max(ceil(total / per_page), 1)
    page = min(page, pages)
    start = (page - 1) * per_page
    page_items = tutors[start : start + per_page]

    subject_counts = Counter(
        c.split()[0]
        for t in tutors
        for c in (t.courses_csv or "").split(";")
        if c.strip()
    )
    # Filter courses by selected subject (if any)
    course_counts = Counter(
        c.strip()
        for t in tutors
        for c in (t.courses_csv or "").split(";")
        if c.strip() and (not subject or c.strip().upper().startswith(subject))
    )
    
    # Filter locations: only show locations from tutors who match current subject AND course filters
    def tutor_matches_filters(t, subj, crs):
        tutor_courses = [c.strip().upper() for c in (t.courses_csv or "").split(";") if c.strip()]
        # If course is selected, tutor must teach that exact course
        if crs and crs not in tutor_courses:
            return False
        # If only subject is selected, tutor must teach at least one course in that subject
        if subj and not crs:
            if not any(c.startswith(subj) for c in tutor_courses):
                return False
        return True
    
    location_counts = Counter(
        m
        for t in tutors
        for m in (t.meeting_options or "").split(",")
        if m and tutor_matches_filters(t, subject, course)
    )

    facets = {
        "subjects": OrderedDict(sorted(subject_counts.items())),
        "courses": OrderedDict(sorted(course_counts.items())),
        "locations": OrderedDict(sorted(location_counts.items())),
    }

    chips = []
    if subject:
        chips.append({"key": "subject", "label": subject})
    if course:
        chips.append({"key": "course", "label": course})
    if location:
        chips.append({"key": "location", "label": location.title()})

    def adapt(t: Tutor):
        return {
            "slug": t.slug,
            "name": t.user.name if t.user else t.slug,
            "first": (
                t.user.name.split()[0]
                if t.user and t.user.name
                else "Tutor"
            ),
            "avatar": "",
            "headline": t.headline,
            "desc": t.bio,
            "rating": (t.rating_avg or 0) / 10.0,
            "rating_count": t.rating_count,
            "hours": t.hours_total_min,
            "courses": [
                c.strip()
                for c in (t.courses_csv or "").split(";")
                if c.strip()
            ],
            "locations": [
                m for m in (t.meeting_options or "").split(",") if m
            ],
        }

    view_items = [adapt(t) for t in page_items]

    return render_template(
        "results.html",
        q=(request.args.get("q") or ""),
        sort=sort,
        tutors=view_items,
        total=total,
        page=page,
        pages=pages,
        facets=facets,
        chips=chips,
    )


@app.get("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    if not q or len(q) > 200:
        return jsonify([])
    q_lower = q.casefold()
    log.info("Search query: %s", q)
    members = load_team_data()
    results = [
        m
        for m in members
        if q_lower in f"{m.get('name', '')} {m.get('role', '')}".casefold()
    ]
    return jsonify(
        [
            {
                "name": m.get("name", ""),
                "role": m.get("role", ""),
                "slug": m.get("slug", ""),
            }
            for m in results
        ]
    )


# -------------------- In-site messaging (legacy contact form) --------------------


@app.post("/messages")
@login_required
def post_message():
    form = request.form if request.form else None
    data = form or (request.get_json(silent=True) or {})

    to_slug = (data.get("to") or data.get("to_slug") or "").strip()
    from_email = (data.get("from_email") or "").strip().lower()
    from_name = (data.get("from_name") or "").strip()
    body = (data.get("body") or "").strip()

    # simple honeypot
    if (data.get("website") or ""):
        abort(400)

    if not (to_slug and from_name and from_email and body):
        abort(400)
    if not from_email.endswith("sfsu.edu"):
        abort(400)
    if not find_member(to_slug):
        abort(404)
    if len(body) > 2000:
        abort(400)

    # TODO: persist to DB in the next milestone
    if form:
        return redirect(
            url_for("member_page", slug=to_slug, sent=1, _anchor="contact")
        )
    return jsonify({"ok": True}), 201


# -------------------- Auth --------------------


@app.route("/signup", methods=["GET", "POST"])
def signup():
    form = SignupForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        with SessionLocal() as db:
            if db.query(User).filter(User.email == email).first():
                form.email.errors.append(
                    "An account with this email already exists."
                )
                return render_template("signup.html", form=form)
            user = User(name=form.name.data.strip())
            user.email = email
            user.set_password(form.password.data)
            db.add(user)
            db.commit()
            login_user(user)
            return redirect(url_for("home_page"))
    return render_template("signup.html", form=form)


@app.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        with SessionLocal() as db:
            user = db.query(User).filter(User.email == email).first()
            if not user or not user.check_password(form.password.data):
                error_msg = "Invalid email or password."
                form.password.errors.append(error_msg)
                return render_template(
                    "login.html", form=form, error=error_msg
                )
            login_user(user)
            next_url = request.args.get("next") or url_for("home_page")
            return redirect(next_url)
    return render_template("login.html", form=form)


@app.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("home_page"))


# -------------------- Become a Tutor --------------------


@app.route("/tutors/new", methods=["GET", "POST"])
@login_required
def become_tutor():
    form = TutorApplicationForm()

    # Prefill-only data for the template (read-only fields in UI)
    prefill = {
        "name": current_user.name if current_user.is_authenticated else "",
        "email": current_user.email
        if current_user.is_authenticated
        else "",
    }

    if form.validate_on_submit():
        # Save up to 3 files if provided
        files_to_save = [
            request.files.get("doc1"),
            request.files.get("doc2"),
            request.files.get("doc3"),
        ]
        saved_paths = save_files(
            [f for f in files_to_save if f],
            UPLOAD_DIR,
        )
        documents_csv = ",".join(saved_paths) if saved_paths else None

        # Persist application
        with SessionLocal() as db:
            app_row = TutorApplication(
                user_id=current_user.id
                if current_user.is_authenticated
                else None,
                name=(
                    prefill["name"]
                    or request.form.get("name", "").strip()
                    or "Unknown"
                ),
                email=prefill["email"]
                or request.form.get("email", "").strip(),
                headline=form.headline.data.strip(),
                bio=form.bio.data.strip(),
                meeting_options=",".join(
                    form.meeting_options.data or []
                ),
                courses_csv=form.courses_csv.data.strip(),
                availability_json=(
                    form.availability_json.data or ""
                ).strip()
                or None,
                documents_csv=documents_csv,
                status="pending",
            )
            db.add(app_row)
            db.commit()

        # Friendly UX: show a banner on the same page
        return redirect(url_for("become_tutor", submitted=1))

    submitted = request.args.get("submitted") == "1"
    if submitted:
        flash(
            "Application submitted. Status: Pending review (24–48 hours).",
            "success",
        )

    return render_template("become_tutor.html", form=form, prefill=prefill)


# -------------------- Tutor dashboard --------------------

@app.route("/tutor/dashboard")
@login_required
def tutor_dashboard():
    """
    Tutor dashboard

    Shows:
      - pending_sessions: session requests to approve/deny
      - upcoming_sessions: future confirmed/approved sessions
      - next_session: the soonest upcoming session
      - message_threads: recent message threads (with latest message)
      - students: distinct students this tutor has met with
      - availability_blocks: availability from tutor_availability_blocks
    """
    # Only allow tutors/admins to access
    if not getattr(current_user, "is_tutor", False):
        abort(403)

    now = datetime.utcnow()

    # Default empty values so the page still renders even if there is no Tutor row
    pending_sessions = []
    upcoming_sessions = []
    next_session = None
    inbox_threads = []
    sent_threads = []
    students = []
    availability_blocks = []

    with SessionLocal() as db:
        # Find the Tutor row for the logged-in user (may not exist for admins)
        tutor = (
            db.query(Tutor)
            .options(joinedload(Tutor.user))
            .filter(Tutor.user_id == current_user.id)
            .first()
        )

        # If there *is* a Tutor profile, load all the dashboard data
        if tutor:
            # --- Pending session requests (Session Requests card) ---
            pending_sessions = (
                db.query(Session)
                .options(joinedload(Session.student))
                .filter(Session.tutor_id == tutor.id)
                .filter(Session.status.in_(["pending", "requested"]))
                .order_by(Session.start_at.asc())
                .limit(5)
                .all()
            )

            # --- Upcoming sessions (Upcoming Tutoring Sessions card) ---
            upcoming_sessions = (
                db.query(Session)
                .options(joinedload(Session.student))
                .filter(Session.tutor_id == tutor.id)
                .filter(Session.status.in_(["approved", "confirmed"]))
                .filter(Session.start_at >= now)
                .order_by(Session.start_at.asc())
                .limit(5)
                .all()
            )

            # Next Session card
            next_session = upcoming_sessions[0] if upcoming_sessions else None

            # --- Messages (Messages card) ---
            # Inbox: threads started by students
            inbox_threads = (
                db.query(MessageThread)
                .options(
                    joinedload(MessageThread.messages),
                    joinedload(MessageThread.student),
                )
                .filter(MessageThread.tutor_id == tutor.id)
                .filter(MessageThread.started_by == "student")
                .order_by(MessageThread.last_message_at.desc())
                .limit(5)
                .all()
            )
            
            # Sent: threads started by tutor
            sent_threads = (
                db.query(MessageThread)
                .options(
                    joinedload(MessageThread.messages),
                    joinedload(MessageThread.student),
                )
                .filter(MessageThread.tutor_id == tutor.id)
                .filter(MessageThread.started_by == "tutor")
                .order_by(MessageThread.last_message_at.desc())
                .limit(50)
                .all()
            )

            # --- Your Students (Your Students card) ---
            student_ids_subq = (
                db.query(Session.student_id)
                .filter(Session.tutor_id == tutor.id)
                .distinct()
                .subquery()
            )

            students = (
                db.query(User)
                .filter(User.id.in_(student_ids_subq))
                .order_by(User.name.asc())
                .limit(6)
                .all()
            )

            # --- Availability (Availability card) ---
            availability_blocks = (
                db.query(TutorAvailabilityBlock)
                .filter(TutorAvailabilityBlock.tutor_id == tutor.id)
                .filter(TutorAvailabilityBlock.is_active == 1)
                .order_by(
                    TutorAvailabilityBlock.weekday.asc(),
                    TutorAvailabilityBlock.start_time.asc(),
                )
                .all()
            )

        # If there is no Tutor row (e.g. admin without a tutor_profile),
        # we just render the dashboard with the default empty data above.

    return render_template(
        "tutor_dashboard.html",
        pending_sessions=pending_sessions,
        upcoming_sessions=upcoming_sessions,
        next_session=next_session,
        inbox_threads=inbox_threads,
        sent_threads=sent_threads,
        students=students,
        availability_blocks=availability_blocks,
        now=now,
    )


# -------------------- Student Dashboard --------------------

@app.route("/student/dashboard")
@login_required
def student_dashboard():
    """
    Student dashboard - shows student's tutoring activity.

    Shows:
      - pending_requests: session requests waiting for tutor approval
      - upcoming_sessions: future confirmed/approved sessions
      - next_session: the soonest upcoming session
      - message_threads: recent message threads with tutors
      - my_tutors: distinct tutors this student has worked with
    """
    now = datetime.utcnow()

    pending_requests = []
    upcoming_sessions = []
    next_session = None
    inbox_threads = []
    sent_threads = []
    my_tutors = []

    with SessionLocal() as db:
        # --- Pending requests (waiting for tutor approval) ---
        pending_requests = (
            db.query(Session)
            .options(joinedload(Session.tutor).joinedload(Tutor.user))
            .filter(Session.student_id == current_user.id)
            .filter(Session.status.in_(["pending", "requested"]))
            .order_by(Session.start_at.asc())
            .limit(5)
            .all()
        )

        # --- Upcoming sessions (confirmed/approved) ---
        upcoming_sessions = (
            db.query(Session)
            .options(joinedload(Session.tutor).joinedload(Tutor.user))
            .filter(Session.student_id == current_user.id)
            .filter(Session.status.in_(["approved", "confirmed"]))
            .filter(Session.start_at >= now)
            .order_by(Session.start_at.asc())
            .limit(5)
            .all()
        )

        # Next session
        next_session = upcoming_sessions[0] if upcoming_sessions else None

        # --- Messages with tutors (Inbox - from tutors) ---
        inbox_threads = (
            db.query(MessageThread)
            .options(
                joinedload(MessageThread.messages),
                joinedload(MessageThread.tutor).joinedload(Tutor.user),
            )
            .filter(MessageThread.student_id == current_user.id)
            .filter(MessageThread.started_by == "tutor")
            .order_by(MessageThread.last_message_at.desc())
            .limit(50)
            .all()
        )
        
        # --- Sent messages (started by student) ---
        sent_threads = (
            db.query(MessageThread)
            .options(
                joinedload(MessageThread.messages),
                joinedload(MessageThread.tutor).joinedload(Tutor.user),
            )
            .filter(MessageThread.student_id == current_user.id)
            .filter(MessageThread.started_by == "student")
            .order_by(MessageThread.last_message_at.desc())
            .limit(50)
            .all()
        )

        # --- My Tutors (tutors I've had sessions with) ---
        tutor_ids_subq = (
            db.query(Session.tutor_id)
            .filter(Session.student_id == current_user.id)
            .distinct()
            .subquery()
        )

        my_tutors = (
            db.query(Tutor)
            .options(joinedload(Tutor.user))
            .filter(Tutor.id.in_(tutor_ids_subq))
            .limit(6)
            .all()
        )

    return render_template(
        "student_dashboard.html",
        pending_requests=pending_requests,
        upcoming_sessions=upcoming_sessions,
        next_session=next_session,
        inbox_threads=inbox_threads,
        sent_threads=sent_threads,
        my_tutors=my_tutors,
        now=now,
    )


# -------------------- Edit Student Profile --------------------


@app.route("/profile/edit", methods=["GET", "POST"])
@login_required
def edit_profile():
    """
    Allow any logged-in user to edit their basic profile (name).
    """
    with SessionLocal() as db:
        user = db.query(User).filter(User.id == current_user.id).first()
        if not user:
            abort(404)

        if request.method == "POST":
            name = (request.form.get("name") or "").strip()

            if name:
                user.name = name
                db.commit()
                flash("Your profile has been updated.", "success")
            else:
                flash("Name cannot be empty.", "danger")

            return redirect(url_for("edit_profile"))

        return render_template("edit_profile.html", user=user)


# -------------------- Edit Tutor Profile (from Dashboard) --------------------

@app.route("/tutor/profile/edit", methods=["GET", "POST"])
@login_required
def edit_tutor_profile():
    """
    Allow a tutor to edit *their own* profile (name, headline, bio, avatar).

    Only accessible to logged-in users who are marked as tutors and who have a
    Tutor row associated with their User account.
    """
    if not getattr(current_user, "is_tutor", False):
        abort(403)

    with SessionLocal() as db:
        tutor = (
            db.query(Tutor)
            .options(joinedload(Tutor.user))
            .filter(Tutor.user_id == current_user.id)
            .first()
        )
        if not tutor:
            abort(404)

        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            headline = (request.form.get("headline") or "").strip()
            bio = (request.form.get("bio") or "").strip()

            # Update basic fields
            if name and tutor.user:
                tutor.user.name = name
            tutor.headline = headline or None
            tutor.bio = bio or None

            # Avatar upload (optional)
            avatar_file = request.files.get("avatar")
            if avatar_file and avatar_file.filename:
                ext = Path(avatar_file.filename).suffix.lower()
                if ext in {".png", ".jpg", ".jpeg"}:
                    avatar_dir = UPLOAD_DIR / "avatars"
                    ensure_dir(avatar_dir)

                    # Overwrite existing avatar for this tutor
                    filename = secure_filename(f"tutor-{tutor.id}-avatar{ext}")
                    avatar_path = avatar_dir / filename
                    avatar_file.save(str(avatar_path))

                    rel = str(avatar_path.relative_to(STATIC_DIR).as_posix())
                    # Assumes Tutor has an `avatar_url` string column
                    tutor.avatar_url = rel
                else:
                    flash(
                        "Please upload a PNG or JPG image for your avatar.",
                        "danger",
                    )

            db.commit()
            flash("Your profile has been updated.", "success")
            return redirect(url_for("tutor_profile", slug=tutor.slug))

        # GET – render the edit form
        return render_template("edit_tutor_profile.html", tutor=tutor)


# -------------------- Tutor Availability --------------------

@app.route("/tutor/availability", methods=["GET", "POST"])
@login_required
def tutor_availability():
    """
    Manage tutor availability.

    - Per-day toggle (clear all blocks for that weekday).
    - Per-block create/update/delete.
    """
    if not getattr(current_user, "is_tutor", False):
        abort(403)

    with SessionLocal() as db:
        tutor = (
            db.query(Tutor)
            .options(joinedload(Tutor.availability_blocks))
            .filter(Tutor.user_id == current_user.id)
            .first()
        )
        if not tutor:
            abort(404)

        if request.method == "POST":
            action = (request.form.get("action") or "save").strip()

            # Common weekday parsing (may be blank for some actions)
            weekday_raw = (request.form.get("weekday") or "").strip()
            try:
                weekday = int(weekday_raw) if weekday_raw != "" else None
            except ValueError:
                weekday = None

            # --- Clear all blocks for a given weekday (toggle OFF) ---
            if action == "clear_day" and weekday is not None:
                (
                    db.query(TutorAvailabilityBlock)
                    .filter(
                        TutorAvailabilityBlock.tutor_id == tutor.id,
                        TutorAvailabilityBlock.weekday == weekday,
                    )
                    .delete(synchronize_session=False)
                )
                db.commit()
                flash("Day marked as not available.", "success")
                return redirect(url_for("tutor_availability"))

            # --- Delete a single block ---
            if action == "delete":
                block_id_raw = (request.form.get("block_id") or "").strip()
                try:
                    block_id = int(block_id_raw)
                except (TypeError, ValueError):
                    block_id = None

                if block_id:
                    block = (
                        db.query(TutorAvailabilityBlock)
                        .filter(
                            TutorAvailabilityBlock.id == block_id,
                            TutorAvailabilityBlock.tutor_id == tutor.id,
                        )
                        .first()
                    )
                    if block:
                        db.delete(block)
                        db.commit()
                        flash("Availability block removed.", "success")
                return redirect(url_for("tutor_availability"))

            # --- Create / update a block (action = "save" or default) ---
            start_time_str = (request.form.get("start_time") or "").strip()
            end_time_str = (request.form.get("end_time") or "").strip()
            meeting_options = (request.form.get("meeting_options") or "").strip()
            courses = (request.form.get("courses") or "").strip()

            errors: list[str] = []

            if weekday is None or weekday < 0 or weekday > 6:
                errors.append("Please choose a valid weekday.")

            try:
                start_time = datetime.strptime(start_time_str, "%H:%M").time()
                end_time = datetime.strptime(end_time_str, "%H:%M").time()
            except ValueError:
                start_time = end_time = None
                errors.append("Please enter valid times in HH:MM format.")
            else:
                if start_time >= end_time:
                    errors.append("End time must be after start time.")

            if errors:
                for msg in errors:
                    flash(msg, "danger")
                return redirect(url_for("tutor_availability"))

            block_id_raw = (request.form.get("block_id") or "").strip()
            try:
                block_id = int(block_id_raw) if block_id_raw else None
            except ValueError:
                block_id = None

            if block_id:
                # Update existing block
                block = (
                    db.query(TutorAvailabilityBlock)
                    .filter(
                        TutorAvailabilityBlock.id == block_id,
                        TutorAvailabilityBlock.tutor_id == tutor.id,
                    )
                    .first()
                )
                if block:
                    block.weekday = weekday
                    block.start_time = start_time
                    block.end_time = end_time
                    block.meeting_options = meeting_options or None
                    block.courses = courses or None
            else:
                # Create new block (used by "+ Add time block")
                block = TutorAvailabilityBlock(
                    tutor_id=tutor.id,
                    weekday=weekday,
                    start_time=start_time,
                    end_time=end_time,
                    meeting_options=meeting_options or None,
                    courses=courses or None,
                    is_active=1,
                )
                db.add(block)

            db.commit()
            flash("Availability saved.", "success")
            return redirect(url_for("tutor_availability"))

        # --- GET: load all active blocks for this tutor ---
        availability_blocks = (
            db.query(TutorAvailabilityBlock)
            .filter(TutorAvailabilityBlock.tutor_id == tutor.id)
            .filter(TutorAvailabilityBlock.is_active == 1)
            .order_by(
                TutorAvailabilityBlock.weekday.asc(),
                TutorAvailabilityBlock.start_time.asc(),
            )
            .all()
        )

        # Courses to show in the dropdown (from this tutor's profile)
        tutor_courses: list[str] = []
        if getattr(tutor, "courses_csv", None):
            tutor_courses = [
                c.strip()
                for c in (tutor.courses_csv or "").split(";")
                if c.strip()
            ]

    return render_template(
        "tutor_availability.html",
        tutor=tutor,
        availability_blocks=availability_blocks,
        tutor_courses=tutor_courses,
    )


# -------------------- Errors --------------------


@app.errorhandler(404)
def not_found(_e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(_e):
    return render_template("500.html"), 500


# -------------------- Entrypoint --------------------


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    host = os.getenv("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=os.getenv("FLASK_DEBUG") == "1")


# -------------------- Admin commands --------------------


def admin_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapped(*args, **kwargs):
        if (
            not current_user.is_authenticated
            or getattr(current_user, "role", None) != "admin"
        ):
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped