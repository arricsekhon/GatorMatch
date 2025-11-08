from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_wtf import FlaskForm
from wtforms import HiddenField, SubmitField
from application.db import SessionLocal
from application.models import TutorApplication, User
from flask_login import current_user, login_required

bp = Blueprint("admin", __name__, url_prefix="/admin")


class CSRFOnlyForm(FlaskForm):
    pass


@bp.before_request
@login_required
def _must_be_admin():
    if not current_user.is_authenticated or current_user.role != "admin":
        abort(403)


@bp.get("/")
def dashboard():
    # simple counters
    with SessionLocal() as db:
        pending = db.query(TutorApplication).filter(
            TutorApplication.status == "pending").count()
        approved = db.query(TutorApplication).filter(
            TutorApplication.status == "approved").count()
        rejected = db.query(TutorApplication).filter(
            TutorApplication.status == "rejected").count()
    return render_template("admin/dashboard.html", pending=pending, approved=approved, rejected=rejected)


@bp.get("/applications")
def list_apps():
    with SessionLocal() as db:
        q = db.query(TutorApplication).order_by(
            TutorApplication.created_at.desc())
        status = (request.args.get("status") or "").strip()
        if status in {"pending", "approved", "rejected"}:
            q = q.filter(TutorApplication.status == status)
        apps = q.all()
    form = CSRFOnlyForm()
    return render_template("admin/apps.html", apps=apps, form=form)


@bp.post("/applications/<int:app_id>/approve")
def approve_app(app_id: int):
    form = CSRFOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    with SessionLocal() as db:
        app = db.get(TutorApplication, app_id)
        if not app:
            abort(404)
        app.status = "approved"

        # promote linked user (by user_id or fallback to email lookup)
        user = None
        if app.user_id:
            user = db.get(User, app.user_id)
        if not user and app.email:
            user = db.query(User).filter(
                User.email == app.email.lower()).first()
        if user:
            if user.role != "admin":  # keep admins as admin
                user.role = "tutor"
        db.commit()
    flash("Application approved. User promoted to tutor.", "success")
    return redirect(url_for("admin.list_apps"))


@bp.post("/applications/<int:app_id>/reject")
def reject_app(app_id: int):
    form = CSRFOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    with SessionLocal() as db:
        app = db.get(TutorApplication, app_id)
        if not app:
            abort(404)
        app.status = "rejected"
        db.commit()
    flash("Application rejected.", "info")
    return redirect(url_for("admin.list_apps"))

# (Optional) promote a specific user to admin by email (quick tool)


class PromoteForm(FlaskForm):
    email = HiddenField()
    submit = SubmitField("Promote")


@bp.post("/users/<int:user_id>/make-admin")
def make_admin(user_id: int):
    form = CSRFOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if not u:
            abort(404)
        u.role = "admin"
        db.commit()
    flash(f"Promoted {u.email} to admin.", "success")
    return redirect(url_for("admin.dashboard"))
