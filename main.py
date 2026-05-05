import html
import json
import os
import re
import random
import smtplib
from datetime import date, datetime, timedelta
from functools import wraps
from email.message import EmailMessage

import requests
from flask import Flask, render_template, redirect, session, url_for, request, abort
from flask_bootstrap import Bootstrap
from flask_ckeditor import CKEditor
from flask_gravatar import Gravatar
from flask_login import UserMixin, login_user, LoginManager, current_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import ForeignKey, Integer, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from werkzeug.security import check_password_hash, generate_password_hash

from forms import CommentForm, CreatePostForm, ForgotPasswordForm, LoginForm, OTPForm, RegisterForm, ResetPasswordForm

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env_file(env_path):
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            cleaned_value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), cleaned_value)


load_env_file(os.path.join(BASE_DIR, ".env"))

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("FLASK_SECRET_KEY", '8BYkEfBA6O6donzWlSihBXox7C0sKR6b')
ckeditor = CKEditor(app)
Bootstrap(app)
login_manager = LoginManager(app)
guvava = Gravatar(app)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///blog.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

Base = declarative_base()
user_id = 0

STOP_WORDS = {
    "a", "about", "all", "also", "an", "and", "are", "as", "at", "be", "been", "blog",
    "but", "by", "can", "for", "from", "how", "if", "in", "into", "is", "it", "its",
    "of", "on", "or", "our", "that", "the", "their", "them", "this", "to", "up", "use",
    "user", "users", "using", "was", "we", "what", "when", "which", "will", "with", "you",
    "your"
}
OTP_EXPIRY_MINUTES = 5
OTP_RESEND_COOLDOWN_SECONDS = 60


class User(UserMixin, db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(250), nullable=False)
    email = db.Column(db.String(), unique=True, nullable=False)
    password = db.Column(db.String(30), nullable=False)
    is_verified = db.Column(db.Boolean, nullable=False, default=True)
    registration_otp = db.Column(db.String(6), nullable=True)
    registration_otp_expires_at = db.Column(db.String(32), nullable=True)
    registration_otp_sent_at = db.Column(db.String(32), nullable=True)
    reset_otp = db.Column(db.String(6), nullable=True)
    reset_otp_expires_at = db.Column(db.String(32), nullable=True)
    reset_otp_sent_at = db.Column(db.String(32), nullable=True)
    posts = relationship("BlogPost", back_populates="author")
    comments = relationship("Comments", back_populates="author")


class BlogPost(db.Model):
    __tablename__ = "blog_posts"
    id = db.Column(db.Integer, primary_key=True)
    author = relationship("User", back_populates='posts')
    author_id = db.Column(Integer, ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(250), unique=True, nullable=False)
    subtitle = db.Column(db.String(250), nullable=False)
    date = db.Column(db.String(250), nullable=False)
    body = db.Column(db.Text, nullable=False)
    img_url = db.Column(db.String(250), nullable=False)
    summary = db.Column(db.Text, nullable=False, default="")
    tags = db.Column(db.String(250), nullable=False, default="")
    comments = relationship("Comments", back_populates="parent_post", cascade="all, delete-orphan")

    @property
    def tag_list(self):
        return [tag.strip() for tag in (self.tags or "").split(",") if tag.strip()]


class Comments(db.Model):
    __tablename__ = "comments"
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String, nullable=False)
    author = relationship("User", back_populates="comments")
    author_id = db.Column(db.String, ForeignKey('user.name'), nullable=False)
    parent_post = relationship("BlogPost", back_populates="comments")
    parent_post_id = db.Column(db.Integer, ForeignKey("blog_posts.id"), nullable=False)


def is_logged_in():
    return current_user.is_authenticated


def utc_now():
    return datetime.utcnow()


def to_timestamp(value):
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


def parse_timestamp(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def generate_otp():
    return f"{random.randint(0, 999999):06d}"


def password_is_hashed(password_value):
    return isinstance(password_value, str) and password_value.startswith(("pbkdf2:", "scrypt:"))


def set_password(user, raw_password):
    user.password = generate_password_hash(raw_password)


def verify_and_upgrade_password(user, raw_password):
    if password_is_hashed(user.password):
        return check_password_hash(user.password, raw_password)

    if user.password == raw_password:
        set_password(user, raw_password)
        db.session.commit()
        return True
    return False


def send_email_message(recipient, subject, body):
    mail_username = os.environ.get("MAIL_USERNAME")
    mail_app_password = os.environ.get("MAIL_APP_PASSWORD")

    if not mail_username or not mail_app_password:
        raise RuntimeError("MAIL_USERNAME and MAIL_APP_PASSWORD must be set in .env.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = mail_username
    message["To"] = recipient
    message.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(mail_username, mail_app_password)
        smtp.send_message(message)


def otp_cooldown_remaining(sent_at_value):
    sent_at = parse_timestamp(sent_at_value)
    if sent_at is None:
        return 0
    elapsed = (utc_now() - sent_at).total_seconds()
    remaining = OTP_RESEND_COOLDOWN_SECONDS - int(elapsed)
    return remaining if remaining > 0 else 0


def assign_registration_otp(user):
    now = utc_now()
    user.registration_otp = generate_otp()
    user.registration_otp_expires_at = to_timestamp(now + timedelta(minutes=OTP_EXPIRY_MINUTES))
    user.registration_otp_sent_at = to_timestamp(now)


def assign_reset_otp(user):
    now = utc_now()
    user.reset_otp = generate_otp()
    user.reset_otp_expires_at = to_timestamp(now + timedelta(minutes=OTP_EXPIRY_MINUTES))
    user.reset_otp_sent_at = to_timestamp(now)


def send_registration_otp(user):
    assign_registration_otp(user)
    db.session.commit()
    send_email_message(
        user.email,
        "Verify your blog account",
        (
            f"Hello {user.name},\n\n"
            f"Your verification OTP is {user.registration_otp}.\n"
            f"It will expire in {OTP_EXPIRY_MINUTES} minutes.\n\n"
            "If you did not request this, you can ignore this email."
        ),
    )


def send_reset_otp(user):
    assign_reset_otp(user)
    db.session.commit()
    send_email_message(
        user.email,
        "Reset your blog password",
        (
            f"Hello {user.name},\n\n"
            f"Your password reset OTP is {user.reset_otp}.\n"
            f"It will expire in {OTP_EXPIRY_MINUTES} minutes.\n\n"
            "If you did not request this, you can ignore this email."
        ),
    )


def clear_registration_otp(user):
    user.registration_otp = None
    user.registration_otp_expires_at = None
    user.registration_otp_sent_at = None


def clear_reset_otp(user):
    user.reset_otp = None
    user.reset_otp_expires_at = None
    user.reset_otp_sent_at = None


def set_auth_notice(message=None, error=None):
    if message is not None:
        session["auth_message"] = message
    if error is not None:
        session["auth_error"] = error


def pop_auth_notice():
    return session.pop("auth_message", None), session.pop("auth_error", None)


def strip_rich_text(raw_text):
    cleaned_text = re.sub(r"<[^>]+>", " ", raw_text or "")
    cleaned_text = html.unescape(cleaned_text)
    return re.sub(r"\s+", " ", cleaned_text).strip()


def normalize_tags(raw_tags):
    tokens = re.split(r"[,#\n]+", raw_tags or "")
    normalized = []
    seen = set()
    for token in tokens:
        cleaned = re.sub(r"\s+", " ", token).strip().lower()
        if not cleaned:
            continue
        title_case = cleaned.title()
        if title_case.lower() in seen:
            continue
        seen.add(title_case.lower())
        normalized.append(title_case)
    return ", ".join(normalized[:6])


def fallback_summary_and_tags(title, subtitle, body):
    plain_text = strip_rich_text(" ".join([title or "", subtitle or "", body or ""]))
    sentences = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", plain_text) if segment.strip()]
    summary = " ".join(sentences[:2]).strip()
    if not summary:
        summary = " ".join(plain_text.split()[:50]).strip()
    if len(summary) > 300:
        summary = summary[:300].rsplit(" ", 1)[0]

    words = re.findall(r"[A-Za-z][A-Za-z0-9+-]{2,}", plain_text.lower())
    ranked = []
    seen = set()
    for word in words:
        if word in STOP_WORDS or word in seen:
            continue
        seen.add(word)
        ranked.append(word.title())
    tags = ", ".join(ranked[:5])
    return summary, tags


def fallback_title(title, subtitle, body):
    if title and title.strip():
        return title.strip()

    plain_text = strip_rich_text(" ".join([subtitle or "", body or ""]))
    sentences = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", plain_text) if segment.strip()]
    if sentences:
        base = sentences[0]
    else:
        base = " ".join(plain_text.split()[:10]).strip()
    if len(base) > 70:
        base = base[:70].rsplit(" ", 1)[0]
    return base or "Untitled Post"


def fallback_draft_from_topic(topic):
    clean_topic = (topic or "").strip() or "Modern web development"
    title = clean_topic if len(clean_topic) <= 70 else clean_topic[:70].rsplit(" ", 1)[0]
    subtitle = f"Key ideas, challenges, and practical takeaways about {clean_topic.lower()}"
    body = (
        f"<p>{clean_topic} is a subject worth exploring because it connects practical work with larger ideas. "
        "A strong article on this topic should give readers a clear entry point, explain why the topic matters, "
        "and offer enough detail to feel useful without becoming difficult to follow.</p>"
        f"<p>One good way to understand {clean_topic.lower()} is to break it into smaller themes. "
        "That might include the background of the topic, the current challenges surrounding it, and the tools or "
        "approaches people use to work with it effectively. When those parts are explained in order, the article "
        "becomes easier to read and more persuasive.</p>"
        f"<p>In practice, {clean_topic.lower()} becomes more interesting when examples are included. "
        "Examples help connect abstract explanation to real work, which is especially useful in educational and "
        "project-based writing. They also make the article feel more grounded and credible.</p>"
        "<p>A useful closing section should summarize the idea, highlight its relevance, and point toward future "
        "possibilities. That structure gives the article a satisfying shape and leaves the reader with something "
        "clear to remember.</p>"
    )
    summary, tags = fallback_summary_and_tags(title, subtitle, body)
    return title, subtitle, body, summary, tags


def generate_ai_content(title, subtitle, body):
    fallback_title_text = fallback_title(title, subtitle, body)
    fallback_summary, fallback_tags = fallback_summary_and_tags(title, subtitle, body)
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    if not api_key or not model:
        return fallback_title_text, fallback_summary, fallback_tags, (
            "Generated with the built-in fallback. Set GEMINI_API_KEY in your .env file for live Gemini output."
        )

    prompt_payload = {
        "title": title,
        "subtitle": subtitle,
        "body": strip_rich_text(body),
    }

    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "contents": [
                    {
                        "parts": [
                            {
                                "text": (
                                    "You are helping a blog editor. Return valid JSON with keys "
                                    "'title', 'summary', and 'tags'. The title should be concise and engaging. "
                                    "The summary should be 2 to 3 plain-text sentences for a TLDR block. "
                                    "The tags field should be an array of 3 to 6 short topic tags. "
                                    f"Source content: {json.dumps(prompt_payload)}"
                                )
                            }
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.6,
                    "responseMimeType": "application/json",
                },
            },
            timeout=30,
        )
        response.raise_for_status()
        content = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(content)
        generated_title = (parsed.get("title") or "").strip() or fallback_title_text
        summary = (parsed.get("summary") or "").strip() or fallback_summary
        tags = normalize_tags(", ".join(parsed.get("tags") or [])) or fallback_tags
        return generated_title, summary, tags, f"Generated with Gemini model {model}."
    except (requests.RequestException, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return fallback_title_text, fallback_summary, fallback_tags, (
            "Gemini request failed, so the built-in fallback was used instead."
        )


def generate_ai_draft_from_topic(topic):
    fallback_title_text, fallback_subtitle, fallback_body, fallback_summary, fallback_tags = fallback_draft_from_topic(topic)
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    if not api_key or not model:
        return fallback_title_text, fallback_subtitle, fallback_body, fallback_summary, fallback_tags, (
            "Draft generated with the built-in fallback. Set GEMINI_API_KEY in your .env file for live Gemini output."
        )

    prompt_payload = {
        "topic": (topic or "").strip(),
    }

    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "contents": [
                    {
                        "parts": [
                            {
                                "text": (
                                    "You are helping a blog author draft a full article. Return valid JSON with keys "
                                    "'title', 'subtitle', 'body', 'summary', and 'tags'. "
                                    "The body must be clean HTML with multiple <p> paragraphs, suitable for storing in a blog editor. "
                                    "The title should be concise and engaging. The subtitle should be one sentence. "
                                    "The summary should be 2 to 3 sentences in plain text. "
                                    "The tags field should be an array of 3 to 6 short topic tags. "
                                    f"Topic request: {json.dumps(prompt_payload)}"
                                )
                            }
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.8,
                    "responseMimeType": "application/json",
                },
            },
            timeout=45,
        )
        response.raise_for_status()
        content = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(content)
        generated_title = (parsed.get("title") or "").strip() or fallback_title_text
        generated_subtitle = (parsed.get("subtitle") or "").strip() or fallback_subtitle
        generated_body = (parsed.get("body") or "").strip() or fallback_body
        generated_summary = (parsed.get("summary") or "").strip() or fallback_summary
        generated_tags = normalize_tags(", ".join(parsed.get("tags") or [])) or fallback_tags
        return (
            generated_title,
            generated_subtitle,
            generated_body,
            generated_summary,
            generated_tags,
            f"Full draft generated with Gemini model {model}.",
        )
    except (requests.RequestException, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return (
            fallback_title_text,
            fallback_subtitle,
            fallback_body,
            fallback_summary,
            fallback_tags,
            "Gemini draft request failed, so the built-in fallback was used instead.",
        )


def ensure_database_schema():
    inspector = inspect(db.engine)
    existing_columns = {column["name"] for column in inspector.get_columns("blog_posts")}
    user_columns = {column["name"] for column in inspector.get_columns("user")}

    with db.engine.begin() as connection:
        if "summary" not in existing_columns:
            connection.execute(text("ALTER TABLE blog_posts ADD COLUMN summary TEXT NOT NULL DEFAULT ''"))
        if "tags" not in existing_columns:
            connection.execute(text("ALTER TABLE blog_posts ADD COLUMN tags VARCHAR(250) NOT NULL DEFAULT ''"))
        if "is_verified" not in user_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN is_verified BOOLEAN NOT NULL DEFAULT 1"))
        if "registration_otp" not in user_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN registration_otp VARCHAR(6)"))
        if "registration_otp_expires_at" not in user_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN registration_otp_expires_at VARCHAR(32)"))
        if "registration_otp_sent_at" not in user_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN registration_otp_sent_at VARCHAR(32)"))
        if "reset_otp" not in user_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN reset_otp VARCHAR(6)"))
        if "reset_otp_expires_at" not in user_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN reset_otp_expires_at VARCHAR(32)"))
        if "reset_otp_sent_at" not in user_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN reset_otp_sent_at VARCHAR(32)"))


with app.app_context():
    db.create_all()
    ensure_database_schema()


@app.route('/')
def get_all_posts():
    posts = BlogPost.query.order_by(BlogPost.id.desc()).all()
    return render_template("index.html", all_posts=posts)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return render_template("register.html", already_logged_in=True)
    form = RegisterForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        mail = form.email.data.strip().lower()
        password = form.password.data
        existing_user = db.session.query(User).filter_by(email=mail).first()

        if existing_user and existing_user.is_verified:
            error = "You have already registered with this email. Kindly log in instead."
            return render_template("login.html", error=error, form=LoginForm())

        if existing_user and not existing_user.is_verified:
            cooldown = otp_cooldown_remaining(existing_user.registration_otp_sent_at)
            existing_user.name = name
            set_password(existing_user, password)
            if cooldown > 0:
                db.session.commit()
                session["pending_verify_email"] = existing_user.email
                set_auth_notice(message=f"An OTP was already sent recently. Please wait {cooldown} seconds before requesting another.")
                return redirect(url_for("verify_registration"))
            try:
                send_registration_otp(existing_user)
            except RuntimeError as exc:
                db.session.rollback()
                return render_template("register.html", form=form, error=str(exc))
            session["pending_verify_email"] = existing_user.email
            set_auth_notice(message=f"We sent a verification OTP to {existing_user.email}.")
            return redirect(url_for("verify_registration"))

        new_user = User(name=name, email=mail, password="")
        set_password(new_user, password)
        new_user.is_verified = False
        db.session.add(new_user)
        db.session.commit()
        try:
            send_registration_otp(new_user)
        except RuntimeError as exc:
            db.session.rollback()
            return render_template("register.html", form=form, error=str(exc))
        session["pending_verify_email"] = new_user.email
        set_auth_notice(message=f"We sent a verification OTP to {new_user.email}.")
        return redirect(url_for("verify_registration"))
    return render_template("register.html", form=form)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return render_template("login.html", already_logged_in=True)
    form = LoginForm()
    message, notice_error = pop_auth_notice()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = db.session.query(User).filter_by(email=email).first()
        if user:
            if not user.is_verified:
                session["pending_verify_email"] = user.email
                set_auth_notice(error="Your account is not verified yet. Please enter the OTP sent to your email.")
                return redirect(url_for("verify_registration"))
            if verify_and_upgrade_password(user, form.password.data):
                global user_id
                user_id = user.id
                login_user(user)
                return redirect(url_for("get_all_posts"))
            error = "Sorry, your password is wrong."
            return render_template("login.html", error=error, form=form, message=message)
        error = "Sorry, this user does not exist in our database."
        return render_template("login.html", error=error, form=form, message=message)
    return render_template("login.html", form=form, message=message, error=notice_error)


@app.route('/verify-registration', methods=['GET', 'POST'])
def verify_registration():
    if current_user.is_authenticated:
        return redirect(url_for('get_all_posts'))

    pending_email = session.get("pending_verify_email")
    if not pending_email:
        return redirect(url_for("register"))

    user = db.session.query(User).filter_by(email=pending_email).first()
    if user is None:
        session.pop("pending_verify_email", None)
        return redirect(url_for("register"))
    if user.is_verified:
        session.pop("pending_verify_email", None)
        return redirect(url_for("login"))

    form = OTPForm()
    message, error = pop_auth_notice()
    if form.validate_on_submit():
        submitted_otp = form.otp.data.strip()
        expires_at = parse_timestamp(user.registration_otp_expires_at)
        if submitted_otp != (user.registration_otp or ""):
            error = "The OTP you entered is incorrect."
        elif expires_at is None or utc_now() > expires_at:
            error = "That OTP has expired. Please request a new one."
        else:
            user.is_verified = True
            clear_registration_otp(user)
            db.session.commit()
            session.pop("pending_verify_email", None)
            global user_id
            user_id = user.id
            login_user(user)
            return redirect(url_for("get_all_posts"))

    return render_template("verify_registration.html", form=form, email=user.email, message=message, error=error)


@app.route('/resend-registration-otp')
def resend_registration_otp():
    pending_email = session.get("pending_verify_email")
    if not pending_email:
        return redirect(url_for("register"))

    user = db.session.query(User).filter_by(email=pending_email).first()
    if user is None or user.is_verified:
        session.pop("pending_verify_email", None)
        return redirect(url_for("register"))

    cooldown = otp_cooldown_remaining(user.registration_otp_sent_at)
    if cooldown > 0:
        set_auth_notice(message=f"Please wait {cooldown} seconds before requesting another OTP.")
        return redirect(url_for("verify_registration"))

    try:
        send_registration_otp(user)
        set_auth_notice(message=f"A fresh OTP was sent to {user.email}.")
        return redirect(url_for("verify_registration"))
    except RuntimeError as exc:
        set_auth_notice(error=str(exc))
        return redirect(url_for("verify_registration"))


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return render_template("login.html", already_logged_in=True)

    form = ForgotPasswordForm()
    message = None
    error = None
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = db.session.query(User).filter_by(email=email).first()
        if user is None:
            error = "We could not find an account with that email."
        elif not user.is_verified:
            error = "Please verify your account before resetting the password."
        else:
            cooldown = otp_cooldown_remaining(user.reset_otp_sent_at)
            if cooldown > 0:
                message = f"An OTP was sent recently. Please wait {cooldown} seconds before requesting another."
                session["password_reset_email"] = user.email
                return render_template("forgot_password.html", form=form, message=message)
            try:
                send_reset_otp(user)
            except RuntimeError as exc:
                error = str(exc)
            else:
                session["password_reset_email"] = user.email
                return redirect(url_for("reset_password"))
    return render_template("forgot_password.html", form=form, message=message, error=error)


@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if current_user.is_authenticated:
        return render_template("login.html", already_logged_in=True)

    reset_email = session.get("password_reset_email")
    if not reset_email:
        return redirect(url_for("forgot_password"))

    user = db.session.query(User).filter_by(email=reset_email).first()
    if user is None:
        session.pop("password_reset_email", None)
        return redirect(url_for("forgot_password"))

    form = ResetPasswordForm()
    message, error = pop_auth_notice()
    if form.validate_on_submit():
        submitted_otp = form.otp.data.strip()
        expires_at = parse_timestamp(user.reset_otp_expires_at)
        if submitted_otp != (user.reset_otp or ""):
            error = "The OTP you entered is incorrect."
        elif expires_at is None or utc_now() > expires_at:
            error = "That OTP has expired. Please request a new one."
        else:
            set_password(user, form.password.data)
            clear_reset_otp(user)
            db.session.commit()
            session.pop("password_reset_email", None)
            set_auth_notice(message="Your password has been reset. You can log in now.")
            return redirect(url_for("login"))

    return render_template("reset_password.html", form=form, email=user.email, message=message, error=error)


@app.route('/resend-reset-otp')
def resend_reset_otp():
    reset_email = session.get("password_reset_email")
    if not reset_email:
        return redirect(url_for("forgot_password"))

    user = db.session.query(User).filter_by(email=reset_email).first()
    if user is None:
        session.pop("password_reset_email", None)
        return redirect(url_for("forgot_password"))

    cooldown = otp_cooldown_remaining(user.reset_otp_sent_at)
    if cooldown > 0:
        set_auth_notice(message=f"Please wait {cooldown} seconds before requesting another OTP.")
        return redirect(url_for("reset_password"))

    try:
        send_reset_otp(user)
        set_auth_notice(message=f"A fresh OTP was sent to {user.email}.")
        return redirect(url_for("reset_password"))
    except RuntimeError as exc:
        set_auth_notice(error=str(exc))
        return redirect(url_for("reset_password"))


@login_manager.user_loader
def load_user(user_id):
    return User.query.filter(User.id == int(user_id)).first()


@app.route('/logout')
def logout():
    logout_user()
    global user_id
    user_id = 0
    return redirect(url_for('get_all_posts'))


@app.route("/post/<int:post_id>", methods=['GET', 'POST'])
def show_post(post_id):
    requested_post = BlogPost.query.get(post_id)
    if requested_post is None:
        return abort(404)

    if request.method == 'GET':
        comments = db.session.query(Comments).filter_by(parent_post_id=post_id).all()
        form = CommentForm()
        id = current_user.get_id()
        return render_template("post.html", post=requested_post, user_id=id, form=form, comments=comments,
                               is_logged_in=is_logged_in())
    else:
        if not current_user.is_authenticated:
            error = "Please log in to post comments."
            return render_template("login.html", error=error, form=LoginForm())
        author = db.session.query(User).filter_by(id=current_user.id).first()
        new_comment = Comments(text=request.form.get("comment"), author=author, parent_post_id=post_id)
        db.session.add(new_comment)
        db.session.commit()
        form = CommentForm()
        id = current_user.get_id()
        comments = db.session.query(Comments).filter_by(parent_post_id=post_id).all()
        return render_template("post.html", post=requested_post, user_id=id, form=form, comments=comments,
                               is_logged_in=is_logged_in())


@app.route("/about")
def about():
    return render_template("about.html", is_logged_in=is_logged_in())


@app.route("/contact")
def contact():
    return render_template("contact.html", is_logged_in=is_logged_in())


def admin_only(f):
    @wraps(f)
    def wrapper_func(*args, **kwargs):
        if not current_user.is_authenticated or current_user.id != 1:
            return abort(403)
        return f(*args, **kwargs)
    return wrapper_func


@app.route("/admin/dashboard")
@admin_only
def admin_dashboard():
    total_posts = BlogPost.query.count()
    total_users = User.query.count()
    total_comments = Comments.query.count()
    tagged_posts = BlogPost.query.filter(BlogPost.tags != "").count()

    recent_posts = BlogPost.query.order_by(BlogPost.id.desc()).limit(5).all()
    recent_comments = Comments.query.order_by(Comments.id.desc()).limit(6).all()
    newest_users = User.query.order_by(User.id.desc()).limit(5).all()

    dashboard_stats = [
        {
            "label": "Posts",
            "value": total_posts,
            "detail": "Published articles in the blog",
        },
        {
            "label": "Users",
            "value": total_users,
            "detail": "Registered reader accounts",
        },
        {
            "label": "Comments",
            "value": total_comments,
            "detail": "Total conversations across posts",
        },
        {
            "label": "Tagged Posts",
            "value": tagged_posts,
            "detail": "Posts enriched with smart tags",
        },
    ]

    return render_template(
        "dashboard.html",
        dashboard_stats=dashboard_stats,
        recent_posts=recent_posts,
        recent_comments=recent_comments,
        newest_users=newest_users,
        is_logged_in=True,
    )


@app.route("/new-post", methods=["GET", 'POST'])
@admin_only
def add_new_post():
    form = CreatePostForm()
    ai_message = None

    if request.method == "POST" and form.generate_draft.data:
        generated_title, generated_subtitle, generated_body, generated_summary, generated_tags, ai_message = (
            generate_ai_draft_from_topic(form.topic.data)
        )
        form.title.data = generated_title
        form.subtitle.data = generated_subtitle
        form.body.data = generated_body
        form.summary.data = generated_summary
        form.tags.data = generated_tags
        return render_template("make-post.html", form=form, is_logged_in=True, is_edit=False, ai_message=ai_message)

    if request.method == "POST" and form.generate_ai.data:
        generated_title, summary, tags, ai_message = generate_ai_content(
            form.title.data,
            form.subtitle.data,
            form.body.data,
        )
        form.title.data = generated_title
        form.summary.data = summary
        form.tags.data = tags
        return render_template("make-post.html", form=form, is_logged_in=True, is_edit=False, ai_message=ai_message)

    if form.validate_on_submit():
        summary = (form.summary.data or "").strip()
        tags = normalize_tags(form.tags.data)
        if not summary or not tags:
            _, generated_summary, generated_tags, ai_message = generate_ai_content(
                form.title.data,
                form.subtitle.data,
                form.body.data,
            )
            summary = summary or generated_summary
            tags = tags or generated_tags
        new_post = BlogPost(
            title=form.title.data,
            subtitle=form.subtitle.data,
            body=form.body.data,
            img_url=form.img_url.data,
            author=db.session.query(User).filter_by(id=current_user.id).first(),
            date=date.today().strftime("%B %d, %Y"),
            summary=summary,
            tags=tags,
        )
        db.session.add(new_post)
        db.session.commit()
        return redirect(url_for("get_all_posts"))
    return render_template("make-post.html", form=form, is_logged_in=True, is_edit=False, ai_message=ai_message)


@app.route("/edit-post/<int:post_id>", methods=["GET", "POST"])
@admin_only
def edit_post(post_id):
    post = BlogPost.query.get(post_id)
    edit_form = CreatePostForm(
        topic=post.title,
        title=post.title,
        subtitle=post.subtitle,
        img_url=post.img_url,
        body=post.body,
        summary=post.summary,
        tags=post.tags,
    )
    ai_message = None

    if request.method == "POST" and edit_form.generate_draft.data:
        generated_title, generated_subtitle, generated_body, generated_summary, generated_tags, ai_message = (
            generate_ai_draft_from_topic(edit_form.topic.data)
        )
        edit_form.title.data = generated_title
        edit_form.subtitle.data = generated_subtitle
        edit_form.body.data = generated_body
        edit_form.summary.data = generated_summary
        edit_form.tags.data = generated_tags
        return render_template("make-post.html", form=edit_form, is_logged_in=True, is_edit=True, ai_message=ai_message)

    if request.method == "POST" and edit_form.generate_ai.data:
        generated_title, summary, tags, ai_message = generate_ai_content(
            edit_form.title.data,
            edit_form.subtitle.data,
            edit_form.body.data,
        )
        edit_form.title.data = generated_title
        edit_form.summary.data = summary
        edit_form.tags.data = tags
        return render_template("make-post.html", form=edit_form, is_logged_in=True, is_edit=True, ai_message=ai_message)

    if edit_form.validate_on_submit():
        post.title = edit_form.title.data
        post.subtitle = edit_form.subtitle.data
        post.img_url = edit_form.img_url.data
        post.body = edit_form.body.data
        post.summary = (edit_form.summary.data or "").strip()
        post.tags = normalize_tags(edit_form.tags.data)
        if not post.summary or not post.tags:
            _, generated_summary, generated_tags, ai_message = generate_ai_content(
                edit_form.title.data,
                edit_form.subtitle.data,
                edit_form.body.data,
            )
            post.summary = post.summary or generated_summary
            post.tags = post.tags or generated_tags
        db.session.commit()
        return redirect(url_for("show_post", post_id=post.id, is_logged_in=True))

    return render_template("make-post.html", form=edit_form, is_logged_in=True, is_edit=True, ai_message=ai_message)


@app.route("/delete/<int:post_id>")
@admin_only
def delete_post(post_id):
    post_to_delete = BlogPost.query.get(post_id)
    db.session.delete(post_to_delete)
    db.session.commit()
    return redirect(url_for('get_all_posts', is_logged_in=True))


if __name__ == "__main__":
    app.run(debug=True)
