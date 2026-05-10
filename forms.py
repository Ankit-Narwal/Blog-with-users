import re

from flask_ckeditor import CKEditorField
from flask_wtf.file import FileAllowed, FileField
from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, EqualTo, Length, Optional, ValidationError


class CreatePostForm(FlaskForm):
    use_ai = BooleanField("Use AI writing tools")
    topic = StringField("Article Topic")
    title = StringField("Blog Post Title", validators=[DataRequired()])
    subtitle = StringField("Subtitle", validators=[DataRequired()])
    scheduled_for = StringField("Schedule Publish Time", render_kw={"type": "datetime-local"})
    img_url = StringField("Blog Image URL", validators=[Optional()])
    cover_image = FileField(
        "Upload Cover Image",
        validators=[FileAllowed(["jpg", "jpeg", "png", "webp"], "Images only.")],
    )
    body = CKEditorField("Blog Content", validators=[DataRequired()])
    summary = TextAreaField("AI Summary (TLDR)")
    tags = StringField("Suggested Tags")
    generate_draft = SubmitField("Write Full Draft from Topic")
    generate_ai = SubmitField("Generate Title, TLDR and Tags")
    export_pdf = SubmitField("Download PDF Draft")
    submit = SubmitField("Submit Post")

    def validate_img_url(self, field):
        value = (field.data or "").strip()
        if not value:
            return
        if value.startswith("/static/"):
            return
        if re.match(r"^https?://", value, re.IGNORECASE):
            return
        raise ValidationError("Use a valid image URL or upload an image file.")


class RegisterForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=6)])
    name = StringField("Your Name", validators=[DataRequired()])
    Submit = SubmitField("Submit")


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    Submit = SubmitField()


class CommentForm(FlaskForm):
    comment = CKEditorField(validators=[DataRequired()])
    Submit = SubmitField()


class OTPForm(FlaskForm):
    otp = StringField("OTP Code", validators=[DataRequired(), Length(min=6, max=6)])
    submit = SubmitField("Verify OTP")


class ForgotPasswordForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired()])
    submit = SubmitField("Send OTP")


class ResetPasswordForm(FlaskForm):
    otp = StringField("OTP Code", validators=[DataRequired(), Length(min=6, max=6)])
    password = PasswordField("New Password", validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    submit = SubmitField("Reset Password")
