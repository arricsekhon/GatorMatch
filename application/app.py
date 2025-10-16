# application/app.py
from __future__ import annotations

import os
import json
import logging
import re
from pathlib import Path
from functools import lru_cache
from collections import Counter, OrderedDict
from math import ceil
from typing import Iterable

from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, jsonify, abort, redirect, url_for, flash
)
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user
)
from flask_wtf import CSRFProtect
from werkzeug.utils import secure_filename

load_dotenv()

# --- Paths---
BASE_DIR    = Path(__file__).resolve().parent          
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR   = BASE_DIR / "static"
DATA_FILE    = BASE_DIR / "data" / "team1_section4.json"
UPLOAD_DIR   = STATIC_DIR / "uploads"                  # will be created on first use

app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-key")

# Security-focused cookie defaults
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "1") == "1",
)

# Upload policy (aligns with the UI copy: PDF/PNG/JPG, up to 10 MB each, max 3 files)
app.config.setdefault("MAX_CONTENT_LENGTH", 30 * 1024 * 1024)  # hard cap for a single request
ALLOWED_EXTS = {".pdf", ".png", ".jpg", ".jpeg"}

app.logger.setLevel(logging.INFO)
log = app.logger

csrf = CSRFProtect(app)

# -------------------- DB wiring --------------------
from application.db import Base, engine, SessionLocal
from application.models import User, TutorApplication
from application.forms import LoginForm, SignupForm, TutorApplicationForm

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
                c.get("title", "") for c in m.get("contributions", []) if isinstance(c, dict)
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
        if subject and not any(subject == c.split()[0].casefold() for c in t["courses"]):
            return False
        if course and course not in t["courses"]:
            return False
        if location and not any(location == loc.casefold() for loc in t["locations"]):
            return False
        return True

    filtered = [t for t in tutors if matches(t)]

    if sort == "highest":
        filtered.sort(key=lambda t: (t["rating"], t["rating_count"]), reverse=True)
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

    subject_counts = Counter(c.split()[0] for t in filtered for c in t["courses"])
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


@app.route("/results")
def results_page():
    members = load_team_data()
    tutors = [normalize_tutor(m) for m in members]
    view = filter_sort_paginate(tutors, request.args)

    chips = []
    subj = (request.args.get("subject") or "").strip()
    if subj:
        chips.append({"key": "subject", "label": subj.upper()})
    crs = (request.args.get("course") or "").strip()
    if crs:
        chips.append({"key": "course", "label": crs})
    loc = (request.args.get("location") or "").strip()
    if loc:
        chips.append({"key": "location", "label": loc.title()})

    return render_template(
        "results.html",
        q=request.args.get("q", ""),
        sort=(request.args.get("sort") or "best"),
        tutors=view["items"],
        total=view["total"],
        page=view["page"],
        pages=view["pages"],
        facets=view["facets"],
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
        if q_lower in f"{m.get('name','')} {m.get('role','')}".casefold()
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


# -------------------- In-site messaging (route fixed) --------------------
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
        return redirect(url_for("member_page", slug=to_slug, sent=1, _anchor="contact"))
    return jsonify({"ok": True}), 201


# -------------------- Auth --------------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    form = SignupForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        with SessionLocal() as db:
            if db.query(User).filter(User.email == email).first():
                form.email.errors.append("An account with this email already exists.")
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
                form.password.errors.append(error_msg)  # keep inline cue
                return render_template("login.html", form=form, error=error_msg)  # show top alert
            login_user(user)
            next_url = request.args.get("next") or url_for("home_page")
            return redirect(next_url)
    return render_template("login.html", form=form)


@app.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("home_page"))


# -------------------- Become a Tutor (new: full form handling) --------------------
@app.route("/tutors/new", methods=["GET", "POST"])
@login_required
def become_tutor():
    form = TutorApplicationForm()

    # Prefill-only data for the template (read-only fields in UI)
    prefill = {
        "name": current_user.name if current_user.is_authenticated else "",
        "email": current_user.email if current_user.is_authenticated else "",
    }

    if form.validate_on_submit():
        # Save up to 3 files if provided
        files_to_save = [
            request.files.get("doc1"),
            request.files.get("doc2"),
            request.files.get("doc3"),
        ]
        saved_paths = save_files([f for f in files_to_save if f], UPLOAD_DIR)
        documents_csv = ",".join(saved_paths) if saved_paths else None

        # Persist application
        with SessionLocal() as db:
            app_row = TutorApplication(
                user_id=current_user.id if current_user.is_authenticated else None,
                name=prefill["name"] or request.form.get("name", "").strip() or "Unknown",
                email=prefill["email"] or request.form.get("email", "").strip(),
                headline=form.headline.data.strip(),
                bio=form.bio.data.strip(),
                meeting_options=",".join(form.meeting_options.data or []),
                courses_csv=form.courses_csv.data.strip(),
                availability_json=(form.availability_json.data or "").strip() or None,
                documents_csv=documents_csv,
                status="pending",
            )
            db.add(app_row)
            db.commit()

        # Friendly UX: show a banner on the same page
        return redirect(url_for("become_tutor", submitted=1))

    # GET or invalid POST
    submitted = request.args.get("submitted") == "1"
    if submitted:
        flash("Application submitted. Status: Pending review (24–48 hours).", "success")

    return render_template("become_tutor.html", form=form, prefill=prefill)


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
