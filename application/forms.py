from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError

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
