from flask_ckeditor import CKEditorField
from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, EqualTo, Length, URL


class CreatePostForm(FlaskForm):
    topic = StringField("Article Topic")
    title = StringField("Blog Post Title", validators=[DataRequired()])
    subtitle = StringField("Subtitle", validators=[DataRequired()])
    img_url = StringField("Blog Image URL", validators=[DataRequired(), URL()])
    body = CKEditorField("Blog Content", validators=[DataRequired()])
    summary = TextAreaField("AI Summary (TLDR)")
    tags = StringField("Suggested Tags")
    generate_draft = SubmitField("Write Full Draft from Topic")
    generate_ai = SubmitField("Generate Title, TLDR and Tags")
    submit = SubmitField("Submit Post")


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
