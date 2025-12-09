from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, TextAreaField, BooleanField, SelectMultipleField
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError, Optional
from flask_wtf.file import FileField, FileAllowed, FileRequired
import re

def _sfsu_only(_form, field):
    if not field.data or not field.data.lower().endswith("sfsu.edu"):
        raise ValidationError("Use your @sfsu.edu email.")

class LoginForm(FlaskForm):
    email = StringField(
        "Email",
        validators=[DataRequired(), Email(), Length(max=255)],
        render_kw={"placeholder": "name@sfsu.edu"},
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=6, max=128)],
    )
    submit = SubmitField("Log in")

    def validate_email(self, field):
        _sfsu_only(self, field)

class SignupForm(FlaskForm):
    name = StringField(
        "Full name",
        validators=[DataRequired(), Length(max=120)],
    )
    email = StringField(
        "Email",
        validators=[DataRequired(), Email(), Length(max=255)],
        render_kw={"placeholder": "name@sfsu.edu"},
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=6, max=128)],
    )
    confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    submit = SubmitField("Create account")

    def validate_email(self, field):
        _sfsu_only(self, field)

# --- Become a Tutor ----------------------------------------------------------

_COURSE_RE = re.compile(r"\b([A-Z]{2,4})\s?(\d{3})\b", re.IGNORECASE)
_ALLOWED_DOC_EXT = ["pdf", "png", "jpg", "jpeg"]


class TutorApplicationForm(FlaskForm):
    # Tutor info
    headline = StringField("Headline", validators=[DataRequired(), Length(max=80)])
    bio = TextAreaField("Short Bio", validators=[DataRequired(), Length(max=4000)])

    # Meeting options (checkboxes share the same field name)
    meeting_options = SelectMultipleField(
        "Meeting Options",
        choices=[
            ("library", "In-person — SFSU Library"),
            ("mashouf", "In-person — Mashouf"),
            ("zoom", "Zoom (remote)"),
            ("jitsi", "Jitsi Meet (Free/No Account)"),
        ],
        validators=[Optional()],  # custom check below enforces >=1
        coerce=str,
    )

    # Courses / availability payloads from the page script
    courses_csv = StringField("Courses CSV", validators=[DataRequired(), Length(max=1000)])
    availability_json = StringField("Availability JSON", validators=[Optional(), Length(max=8000)])

    # Up to three documents (optional each; at least one is enforced below)
    doc1 = FileField("Document 1", validators=[Optional(), FileAllowed(_ALLOWED_DOC_EXT)])
    doc2 = FileField("Document 2", validators=[Optional(), FileAllowed(_ALLOWED_DOC_EXT)])
    doc3 = FileField("Document 3", validators=[Optional(), FileAllowed(_ALLOWED_DOC_EXT)])

    # Policies
    policies = BooleanField(
        "I certify…", validators=[DataRequired(message="You must agree to the policies.")]
    )

    submit = SubmitField("Submit for Review")

    # --- Custom validators ---

    def validate_meeting_options(self, field):
        if not field.data or len(field.data) < 1:
            raise ValidationError("Select at least one meeting option.")

    def validate_courses_csv(self, field):
        txt = (field.data or "").strip()
        if not txt:
            raise ValidationError("Add at least one course.")
        # Must include at least one token like "CSC 340"
        if not _COURSE_RE.search(txt):
            raise ValidationError("Include at least one course like 'CSC 340'.")

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators=extra_validators)
        # At least one document overall (doc1/2/3) must be provided OR already uploaded in future flow.
        if not any([bool(getattr(self.doc1, "data", None)),
                    bool(getattr(self.doc2, "data", None)),
                    bool(getattr(self.doc3, "data", None))]):
            self.doc1.errors.append("Upload at least one verification document (PDF/PNG/JPG).")
            ok = False
        return ok
