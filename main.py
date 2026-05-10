import html
import io
import json
import math
import os
import re
import random
import secrets
import smtplib
import uuid
from datetime import date, datetime, timedelta
from functools import wraps
from email.message import EmailMessage
from urllib.parse import urlencode

import requests
from flask import Flask, render_template, redirect, session, url_for, request, abort, send_file, flash, jsonify
from flask_bootstrap import Bootstrap
from flask_ckeditor import CKEditor
from flask_gravatar import Gravatar
from flask_login import UserMixin, login_user, LoginManager, current_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import ForeignKey, Integer, inspect, text, or_, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from fpdf import FPDF

from forms import CommentForm, CreatePostForm, ForgotPasswordForm, LoginForm, OTPForm, RegisterForm, ResetPasswordForm

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
DEFAULT_COVER_IMAGE = "/static/img/edit-bg.jpg"


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
os.makedirs(UPLOAD_DIR, exist_ok=True)

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
TEXT_MODEL_FALLBACKS = ["gemini-2.5-flash-lite"]
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class User(UserMixin, db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(250), nullable=False)
    email = db.Column(db.String(), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    is_verified = db.Column(db.Boolean, nullable=False, default=True)
    registration_otp = db.Column(db.String(6), nullable=True)
    registration_otp_expires_at = db.Column(db.String(32), nullable=True)
    registration_otp_sent_at = db.Column(db.String(32), nullable=True)
    reset_otp = db.Column(db.String(6), nullable=True)
    reset_otp_expires_at = db.Column(db.String(32), nullable=True)
    reset_otp_sent_at = db.Column(db.String(32), nullable=True)
    profile_photo = db.Column(db.String(255), nullable=False, default="")
    bio = db.Column(db.Text, nullable=False, default="")
    github_url = db.Column(db.String(255), nullable=False, default="")
    twitter_url = db.Column(db.String(255), nullable=False, default="")
    website_url = db.Column(db.String(255), nullable=False, default="")
    joined_date = db.Column(db.String(32), nullable=False, default="")
    google_id = db.Column(db.String(255), nullable=False, default="")
    posts = relationship("BlogPost", back_populates="author")
    comments = relationship("Comments", back_populates="author")
    reactions = relationship("PostReaction", back_populates="user", cascade="all, delete-orphan")
    bookmarks = relationship("Bookmark", back_populates="user", cascade="all, delete-orphan")

    @property
    def profile_image(self):
        return self.profile_photo or None


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
    created_at = db.Column(db.String(32), nullable=False, default="")
    published_at = db.Column(db.String(32), nullable=False, default="")
    scheduled_for = db.Column(db.String(32), nullable=False, default="")
    views = db.Column(db.Integer, nullable=False, default=0)
    comments = relationship("Comments", back_populates="parent_post", cascade="all, delete-orphan")
    reactions = relationship("PostReaction", back_populates="post", cascade="all, delete-orphan")
    bookmarks = relationship("Bookmark", back_populates="post", cascade="all, delete-orphan")

    @property
    def tag_list(self):
        return [tag.strip() for tag in (self.tags or "").split(",") if tag.strip()]

    @property
    def reading_time(self):
        words = len(strip_rich_text(self.body).split())
        return max(1, math.ceil(words / 200))

    @property
    def listening_time(self):
        words = len(strip_rich_text(self.body).split())
        # Approximate spoken narration pace (words per minute).
        return max(1, math.ceil(words / 150))

    @property
    def like_count(self):
        return sum(1 for reaction in self.reactions if reaction.value == 1)

    @property
    def dislike_count(self):
        return sum(1 for reaction in self.reactions if reaction.value == -1)

    @property
    def bookmark_count(self):
        return len(self.bookmarks)

    @property
    def engagement_score(self):
        return (self.like_count * 2) + self.bookmark_count + len(self.comments) + max(1, self.views // 5)


class Comments(db.Model):
    __tablename__ = "comments"
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String, nullable=False)
    author = relationship("User", back_populates="comments")
    author_id = db.Column(db.String, ForeignKey('user.name'), nullable=False)
    parent_post = relationship("BlogPost", back_populates="comments")
    parent_post_id = db.Column(db.Integer, ForeignKey("blog_posts.id"), nullable=False)
    created_at = db.Column(db.String(32), nullable=False, default="")
    status = db.Column(db.String(32), nullable=False, default="approved")
    moderation_reason = db.Column(db.String(255), nullable=False, default="")

    @property
    def created_label(self):
        return format_comment_timestamp(self.created_at) or "Reader comment"


class PostReaction(db.Model):
    __tablename__ = "post_reactions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, ForeignKey("user.id"), nullable=False)
    post_id = db.Column(db.Integer, ForeignKey("blog_posts.id"), nullable=False)
    value = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.String(32), nullable=False, default="")
    user = relationship("User", back_populates="reactions")
    post = relationship("BlogPost", back_populates="reactions")


class Bookmark(db.Model):
    __tablename__ = "bookmarks"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, ForeignKey("user.id"), nullable=False)
    post_id = db.Column(db.Integer, ForeignKey("blog_posts.id"), nullable=False)
    saved_at = db.Column(db.String(32), nullable=False, default="")
    user = relationship("User", back_populates="bookmarks")
    post = relationship("BlogPost", back_populates="bookmarks")


class NewsletterSubscription(db.Model):
    __tablename__ = "newsletter_subscriptions"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=False, default="")
    subscribed_at = db.Column(db.String(32), nullable=False, default="")


def is_logged_in():
    return current_user.is_authenticated


@app.context_processor
def inject_layout_state():
    return {
        "active_theme": current_theme(),
        "is_admin_user": is_admin_user(current_user),
        "google_oauth_enabled": google_oauth_ready(),
    }


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


def format_timestamp(value):
    parsed = parse_timestamp(value)
    if parsed is None:
        return ""
    return parsed.strftime("%B %d, %Y")


def format_comment_timestamp(value):
    parsed = parse_timestamp(value)
    if parsed is None:
        return ""
    return parsed.strftime("%b %d, %Y at %I:%M %p")


def parse_datetime_local(value):
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    for pattern in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(cleaned, pattern)
        except ValueError:
            continue
    return None


def is_admin_user(user):
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "id", None) == 1)


def generate_otp():
    return f"{random.randint(0, 999999):06d}"


def password_is_hashed(password_value):
    return isinstance(password_value, str) and password_value.startswith(("pbkdf2:", "scrypt:"))


def set_password(user, raw_password):
    user.password = generate_password_hash(raw_password)


def random_password():
    return secrets.token_urlsafe(24)


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


def default_cover_image():
    return DEFAULT_COVER_IMAGE


def build_upload_filename(prefix, original_name, fallback_extension=".png"):
    sanitized_name = secure_filename(original_name or "")
    _, extension = os.path.splitext(sanitized_name)
    if not extension:
        extension = fallback_extension
    return f"{prefix}-{uuid.uuid4().hex}{extension.lower()}"


def save_uploaded_cover_image(file_storage, prefix="cover"):
    if file_storage is None or not getattr(file_storage, "filename", ""):
        return None
    filename = build_upload_filename(prefix, file_storage.filename, fallback_extension=".jpg")
    destination = os.path.join(UPLOAD_DIR, filename)
    file_storage.save(destination)
    return f"/static/uploads/{filename}"


def resolve_cover_image(form):
    uploaded_path = save_uploaded_cover_image(getattr(form, "cover_image", None).data, prefix="cover")
    if uploaded_path:
        return uploaded_path
    typed_value = (form.img_url.data or "").strip()
    return typed_value or default_cover_image()


def sync_post_publication_state(post):
    scheduled = parse_datetime_local(post.scheduled_for)
    published = parse_timestamp(post.published_at)

    if scheduled and published is None and utc_now() >= scheduled:
        post.published_at = to_timestamp(scheduled)
        post.date = scheduled.strftime("%B %d, %Y")
        return True

    if not scheduled and published is None:
        published = parse_timestamp(post.created_at) or utc_now()
        post.published_at = to_timestamp(published)
        post.date = published.strftime("%B %d, %Y")
        return True

    return False


def sync_scheduled_posts():
    changed = False
    for post in BlogPost.query.all():
        changed = sync_post_publication_state(post) or changed
    if changed:
        db.session.commit()


def is_post_visible(post, include_unpublished=False):
    if include_unpublished:
        return True
    published = parse_timestamp(post.published_at)
    scheduled = parse_datetime_local(post.scheduled_for)
    if published is not None:
        return published <= utc_now()
    if scheduled is not None:
        return scheduled <= utc_now()
    return True


def visible_posts(include_unpublished=False):
    sync_scheduled_posts()
    posts = BlogPost.query.order_by(BlogPost.id.desc()).all()
    posts = sorted(posts, key=post_sort_key, reverse=True)
    if include_unpublished:
        return posts
    return [post for post in posts if is_post_visible(post)]


def current_theme():
    return request.cookies.get("theme", "light")


def post_user_reaction(post, user):
    if not getattr(user, "is_authenticated", False):
        return 0
    reaction = PostReaction.query.filter_by(post_id=post.id, user_id=user.id).first()
    return reaction.value if reaction else 0


def post_is_bookmarked(post, user):
    if not getattr(user, "is_authenticated", False):
        return False
    return Bookmark.query.filter_by(post_id=post.id, user_id=user.id).first() is not None


def wants_json_response():
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )


def post_engagement_payload(post, user):
    return {
        "counts": {
            "likes": post.like_count,
            "dislikes": post.dislike_count,
            "bookmarks": post.bookmark_count,
        },
        "currentReaction": post_user_reaction(post, user),
        "isBookmarked": post_is_bookmarked(post, user),
    }


def suggested_posts_for(post, limit=3):
    candidates = [
        candidate for candidate in visible_posts(include_unpublished=False)
        if candidate.id != post.id
    ]
    if not candidates:
        return []
    post_tags = {tag.lower() for tag in post.tag_list}
    tagged_matches = [
        candidate for candidate in candidates
        if post_tags and any(tag.lower() in post_tags for tag in candidate.tag_list)
    ]
    remaining = [candidate for candidate in candidates if candidate not in tagged_matches]
    random.shuffle(tagged_matches)
    random.shuffle(remaining)
    return (tagged_matches + remaining)[:limit]


def post_sort_key(post):
    return parse_timestamp(post.published_at) or parse_timestamp(post.created_at) or utc_now()


def clean_pdf_text(value):
    text_value = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    return text_value.encode("latin-1", "replace").decode("latin-1")


def rich_text_to_plain_text(raw_html):
    normalized = raw_html or ""
    replacements = {
        "</p>": "\n\n",
        "<br>": "\n",
        "<br/>": "\n",
        "<br />": "\n",
        "</li>": "\n",
        "</ul>": "\n",
        "</ol>": "\n",
        "</h1>": "\n\n",
        "</h2>": "\n\n",
        "</h3>": "\n\n",
        "</blockquote>": "\n\n",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    return clean_pdf_text(strip_rich_text(normalized))


def make_article_pdf_response(title, subtitle, author_name, published_date, body, summary="", tags=""):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_title(clean_pdf_text(title or "Article"))
    pdf.set_author(clean_pdf_text(author_name or "Ankit's Blog"))

    pdf.set_font("Helvetica", "B", 22)
    pdf.multi_cell(0, 11, clean_pdf_text(title or "Untitled Post"))
    pdf.ln(2)

    if subtitle:
        pdf.set_font("Helvetica", "", 13)
        pdf.set_text_color(73, 80, 87)
        pdf.multi_cell(0, 8, clean_pdf_text(subtitle))
        pdf.ln(2)

    pdf.set_text_color(33, 37, 41)
    pdf.set_font("Helvetica", "", 11)
    meta_bits = []
    if author_name:
        meta_bits.append(f"Author: {author_name}")
    if published_date:
        meta_bits.append(f"Published: {published_date}")
    if meta_bits:
        pdf.multi_cell(0, 7, clean_pdf_text(" | ".join(meta_bits)))
        pdf.ln(2)

    if summary:
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, "TLDR", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 7, clean_pdf_text(summary))
        pdf.ln(2)

    if tags:
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, "Tags", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 7, clean_pdf_text(tags))
        pdf.ln(2)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Article", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    for paragraph in [part.strip() for part in rich_text_to_plain_text(body).split("\n") if part.strip()]:
        pdf.multi_cell(0, 7, paragraph)
        pdf.ln(1)

    pdf_bytes = bytes(pdf.output())
    download_name = secure_filename(f"{(title or 'article').lower()}.pdf") or "article.pdf"
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        attachment_filename=download_name,
    )


def unique_models(primary_model, fallback_models):
    ordered = []
    for model_name in [primary_model] + list(fallback_models or []):
        cleaned = (model_name or "").strip()
        if cleaned and cleaned not in ordered:
            ordered.append(cleaned)
    return ordered


def gemini_error_message(response):
    try:
        payload = response.json()
        return payload.get("error", {}).get("message") or response.text
    except ValueError:
        return response.text


def request_gemini_json(prompt_text, fallback_payload, primary_model, fallback_models, temperature=0.7, timeout=45):
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    model_candidates = unique_models(primary_model, fallback_models)

    if not api_key or not model_candidates:
        return fallback_payload, None, "Gemini is not configured, so the built-in fallback was used."

    last_error = None
    for model_name in model_candidates:
        for attempt in range(2):
            try:
                response = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent",
                    headers={
                        "x-goog-api-key": api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "contents": [{"parts": [{"text": prompt_text}]}],
                        "generationConfig": {
                            "temperature": temperature,
                            "responseMimeType": "application/json",
                        },
                    },
                    timeout=timeout,
                )
                if response.status_code in (429, 500, 503) and attempt == 0:
                    last_error = gemini_error_message(response)
                    continue
                response.raise_for_status()
                content = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(content), model_name, None
            except (requests.RequestException, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)

    error_detail = last_error or "Gemini request failed."
    return fallback_payload, None, f"Gemini was unavailable, so the built-in fallback was used. Details: {error_detail}"


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
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    prompt_payload = {
        "title": title,
        "subtitle": subtitle,
        "body": strip_rich_text(body),
    }
    fallback_payload = {
        "title": fallback_title_text,
        "summary": fallback_summary,
        "tags": fallback_tags.split(", ") if fallback_tags else [],
    }
    prompt_text = (
        "You are helping a blog editor. Return valid JSON with keys "
        "'title', 'summary', and 'tags'. The title should be concise and engaging. "
        "The summary should be 2 to 3 plain-text sentences for a TLDR block. "
        "The tags field should be an array of 3 to 6 short topic tags. "
        f"Source content: {json.dumps(prompt_payload)}"
    )
    parsed, used_model, fallback_message = request_gemini_json(
        prompt_text,
        fallback_payload=fallback_payload,
        primary_model=model,
        fallback_models=TEXT_MODEL_FALLBACKS,
        temperature=0.6,
        timeout=30,
    )
    generated_title = (parsed.get("title") or "").strip() or fallback_title_text
    summary = (parsed.get("summary") or "").strip() or fallback_summary
    tags_value = parsed.get("tags") or []
    tags = normalize_tags(", ".join(tags_value if isinstance(tags_value, list) else [str(tags_value)])) or fallback_tags
    if used_model:
        return generated_title, summary, tags, f"Generated with Gemini model {used_model}."
    return generated_title, summary, tags, fallback_message


def generate_ai_draft_from_topic(topic):
    fallback_title_text, fallback_subtitle, fallback_body, fallback_summary, fallback_tags = fallback_draft_from_topic(topic)
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    prompt_payload = {
        "topic": (topic or "").strip(),
    }
    fallback_payload = {
        "title": fallback_title_text,
        "subtitle": fallback_subtitle,
        "body": fallback_body,
        "summary": fallback_summary,
        "tags": fallback_tags.split(", ") if fallback_tags else [],
    }
    prompt_text = (
        "You are helping a blog author draft a full article. Return valid JSON with keys "
        "'title', 'subtitle', 'body', 'summary', and 'tags'. "
        "The body must be clean HTML with multiple <p> paragraphs, suitable for storing in a blog editor. "
        "The title should be concise and engaging. The subtitle should be one sentence. "
        "The summary should be 2 to 3 sentences in plain text. "
        "The tags field should be an array of 3 to 6 short topic tags. "
        f"Topic request: {json.dumps(prompt_payload)}"
    )
    parsed, used_model, fallback_message = request_gemini_json(
        prompt_text,
        fallback_payload=fallback_payload,
        primary_model=model,
        fallback_models=TEXT_MODEL_FALLBACKS,
        temperature=0.8,
        timeout=45,
    )
    generated_title = (parsed.get("title") or "").strip() or fallback_title_text
    generated_subtitle = (parsed.get("subtitle") or "").strip() or fallback_subtitle
    generated_body = (parsed.get("body") or "").strip() or fallback_body
    generated_summary = (parsed.get("summary") or "").strip() or fallback_summary
    tags_value = parsed.get("tags") or []
    generated_tags = normalize_tags(", ".join(tags_value if isinstance(tags_value, list) else [str(tags_value)])) or fallback_tags

    if used_model:
        return (
            generated_title,
            generated_subtitle,
            generated_body,
            generated_summary,
            generated_tags,
            f"Full draft generated with Gemini model {used_model}. Add a cover image with a URL or upload before publishing.",
        )

    return (
        generated_title,
        generated_subtitle,
        generated_body,
        generated_summary,
        generated_tags,
        fallback_message,
    )


def fallback_comment_moderation(comment_text):
    plain_text = strip_rich_text(comment_text).lower()
    link_hits = re.findall(r"https?://|www\.|bit\.ly|tinyurl|t\.co", plain_text)
    toxic_terms = ["idiot", "stupid", "hate", "kill", "racist", "slur", "abuse", "trash"]
    spam_terms = ["buy now", "free money", "click here", "earn cash", "promo code"]
    has_toxicity = any(term in plain_text for term in toxic_terms)
    has_spam = any(term in plain_text for term in spam_terms)
    too_many_links = len(link_hits) >= 2

    if has_toxicity:
        return False, "Flagged for abusive or toxic language."
    if has_spam or too_many_links:
        return False, "Flagged as spam or containing suspicious links."
    return True, "Approved by fallback moderation."


def moderate_comment(comment_text, post_title=""):
    fallback_allowed, fallback_reason = fallback_comment_moderation(comment_text)
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    prompt_payload = {
        "post_title": post_title,
        "comment": strip_rich_text(comment_text),
    }
    fallback_payload = {
        "decision": "approve" if fallback_allowed else "reject",
        "reason": fallback_reason,
    }
    prompt_text = (
        "You are moderating a blog comment. Return valid JSON with keys 'decision' and 'reason'. "
        "Reject comments that are spam, toxic, abusive, hateful, or contain suspicious fake links. "
        "Use 'approve' or 'reject' for the decision and give a short plain-text reason. "
        f"Moderation target: {json.dumps(prompt_payload)}"
    )
    parsed, _, _ = request_gemini_json(
        prompt_text,
        fallback_payload=fallback_payload,
        primary_model=model,
        fallback_models=TEXT_MODEL_FALLBACKS,
        temperature=0.2,
        timeout=20,
    )
    decision = (parsed.get("decision") or "").strip().lower()
    reason = (parsed.get("reason") or fallback_reason).strip()
    return decision != "reject", reason


def split_home_sections(posts):
    pool = list(posts)
    if not pool:
        return [], [], [], []

    random.shuffle(pool)
    featured_posts = pool[: min(3, len(pool))]

    remaining = [post for post in pool if post.id not in {item.id for item in featured_posts}]
    random.shuffle(remaining)
    trending_posts = remaining[: min(6, len(remaining))]

    remaining = [post for post in remaining if post.id not in {item.id for item in trending_posts}]
    random.shuffle(remaining)
    latest_posts = remaining[: min(9, len(remaining))]

    remaining = [post for post in remaining if post.id not in {item.id for item in latest_posts}]
    random.shuffle(remaining)
    recommended = remaining[: min(4, len(remaining))]

    return featured_posts, trending_posts, latest_posts, recommended


def search_posts(query_text, limit=60):
    cleaned = (query_text or "").strip()
    if not cleaned:
        return []
    like_pattern = f"%{cleaned}%"
    return (
        BlogPost.query.filter(
            or_(
                BlogPost.title.ilike(like_pattern),
                BlogPost.subtitle.ilike(like_pattern),
                BlogPost.body.ilike(like_pattern),
                BlogPost.tags.ilike(like_pattern),
            )
        )
        .order_by(BlogPost.id.desc())
        .limit(limit)
        .all()
    )


def sorted_comments_for_post(post_id, sort_by):
    base = Comments.query.filter_by(parent_post_id=post_id, status="approved")
    if sort_by == "oldest":
        return base.order_by(Comments.id.asc()).all()
    if sort_by == "relevant":
        return base.order_by(func.length(Comments.text).desc(), Comments.id.desc()).all()
    return base.order_by(Comments.id.desc()).all()


def ensure_database_schema():
    inspector = inspect(db.engine)
    existing_columns = {column["name"] for column in inspector.get_columns("blog_posts")}
    user_columns = {column["name"] for column in inspector.get_columns("user")}
    comment_columns = {column["name"] for column in inspector.get_columns("comments")}

    with db.engine.begin() as connection:
        if "summary" not in existing_columns:
            connection.execute(text("ALTER TABLE blog_posts ADD COLUMN summary TEXT NOT NULL DEFAULT ''"))
        if "tags" not in existing_columns:
            connection.execute(text("ALTER TABLE blog_posts ADD COLUMN tags VARCHAR(250) NOT NULL DEFAULT ''"))
        if "created_at" not in existing_columns:
            connection.execute(text("ALTER TABLE blog_posts ADD COLUMN created_at VARCHAR(32) NOT NULL DEFAULT ''"))
        if "published_at" not in existing_columns:
            connection.execute(text("ALTER TABLE blog_posts ADD COLUMN published_at VARCHAR(32) NOT NULL DEFAULT ''"))
        if "scheduled_for" not in existing_columns:
            connection.execute(text("ALTER TABLE blog_posts ADD COLUMN scheduled_for VARCHAR(32) NOT NULL DEFAULT ''"))
        if "views" not in existing_columns:
            connection.execute(text("ALTER TABLE blog_posts ADD COLUMN views INTEGER NOT NULL DEFAULT 0"))
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
        if "profile_photo" not in user_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN profile_photo VARCHAR(255) NOT NULL DEFAULT ''"))
        if "bio" not in user_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN bio TEXT NOT NULL DEFAULT ''"))
        if "github_url" not in user_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN github_url VARCHAR(255) NOT NULL DEFAULT ''"))
        if "twitter_url" not in user_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN twitter_url VARCHAR(255) NOT NULL DEFAULT ''"))
        if "website_url" not in user_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN website_url VARCHAR(255) NOT NULL DEFAULT ''"))
        if "joined_date" not in user_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN joined_date VARCHAR(32) NOT NULL DEFAULT ''"))
        if "google_id" not in user_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN google_id VARCHAR(255) NOT NULL DEFAULT ''"))
        if "created_at" not in comment_columns:
            connection.execute(text("ALTER TABLE comments ADD COLUMN created_at VARCHAR(32) NOT NULL DEFAULT ''"))
        if "status" not in comment_columns:
            connection.execute(text("ALTER TABLE comments ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'approved'"))
        if "moderation_reason" not in comment_columns:
            connection.execute(text("ALTER TABLE comments ADD COLUMN moderation_reason VARCHAR(255) NOT NULL DEFAULT ''"))


with app.app_context():
    db.create_all()
    ensure_database_schema()
    changed = False
    for user in User.query.all():
        if not user.joined_date:
            user.joined_date = date.today().strftime("%B %d, %Y")
            changed = True
    for post in BlogPost.query.all():
        if not post.created_at:
            created_guess = parse_datetime_local(post.scheduled_for) or utc_now()
            post.created_at = to_timestamp(created_guess)
            changed = True
        if not post.published_at and not post.scheduled_for:
            post.published_at = post.created_at
            changed = True
        if not post.date:
            post.date = format_timestamp(post.published_at) or date.today().strftime("%B %d, %Y")
            changed = True
    for comment in Comments.query.all():
        if not comment.created_at:
            comment.created_at = to_timestamp(utc_now())
            changed = True
    if changed:
        db.session.commit()


@app.route('/')
def get_all_posts():
    posts = visible_posts(include_unpublished=False)
    search_query = (request.args.get("q") or "").strip()
    search_results = []
    if search_query:
        visible_ids = {post.id for post in posts}
        search_results = [post for post in search_posts(search_query, limit=60) if post.id in visible_ids]
    featured_posts, trending_posts, latest_posts, recommended_posts = split_home_sections(posts)
    return render_template(
        "index.html",
        all_posts=posts,
        featured_posts=featured_posts,
        trending_posts=trending_posts,
        latest_posts=latest_posts,
        recommended_posts=recommended_posts,
        search_query=search_query,
        search_results=search_results,
    )


@app.route('/search-suggestions')
def search_suggestions():
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify({"items": []})
    visible_ids = {post.id for post in visible_posts(include_unpublished=False)}
    items = []
    for post in search_posts(query, limit=8):
        if post.id not in visible_ids:
            continue
        items.append(
            {
                "title": post.title,
                "subtitle": post.subtitle,
                "url": url_for("show_post", post_id=post.id),
            }
        )
    return jsonify({"items": items})


@app.route('/subscribe-newsletter', methods=['POST'])
def subscribe_newsletter():
    email = (request.form.get("email") or "").strip().lower()
    name = (request.form.get("name") or "").strip()
    if not email:
        flash("Please enter an email address to subscribe.", "danger")
        return redirect(url_for("get_all_posts"))

    existing = NewsletterSubscription.query.filter_by(email=email).first()
    if existing is None:
        existing = NewsletterSubscription(email=email, name=name, subscribed_at=to_timestamp(utc_now()))
        db.session.add(existing)
    else:
        existing.name = name or existing.name
    db.session.commit()
    flash("You are subscribed to weekly updates.", "success")
    return redirect(url_for("get_all_posts"))


def google_oauth_ready():
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def google_redirect_uri():
    return url_for("google_auth_callback", _external=True)


def build_google_auth_url(state_token):
    params = {
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "redirect_uri": google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "include_granted_scopes": "true",
        "state": state_token,
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def fetch_google_token(auth_code):
    payload = {
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "code": auth_code,
        "grant_type": "authorization_code",
        "redirect_uri": google_redirect_uri(),
    }
    response = requests.post(GOOGLE_TOKEN_URL, data=payload, timeout=20)
    if response.status_code != 200:
        return None
    return response.json()


def fetch_google_userinfo(access_token):
    response = requests.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if response.status_code != 200:
        return None
    return response.json()


@app.route("/auth/google")
def google_auth_start():
    if current_user.is_authenticated:
        return redirect(url_for("get_all_posts"))
    if not google_oauth_ready():
        set_auth_notice(error="Google sign-in is not configured yet. Please use email login.")
        return redirect(url_for("login"))
    state_token = secrets.token_urlsafe(24)
    session["google_oauth_state"] = state_token
    return redirect(build_google_auth_url(state_token))


@app.route("/auth/google/callback")
def google_auth_callback():
    if current_user.is_authenticated:
        return redirect(url_for("get_all_posts"))

    state = request.args.get("state", "")
    saved_state = session.pop("google_oauth_state", "")
    if not state or state != saved_state:
        set_auth_notice(error="Google sign-in failed state verification. Please try again.")
        return redirect(url_for("login"))

    auth_code = request.args.get("code", "")
    if not auth_code:
        set_auth_notice(error="Google sign-in was cancelled or failed.")
        return redirect(url_for("login"))

    token_data = fetch_google_token(auth_code)
    if not token_data or not token_data.get("access_token"):
        set_auth_notice(error="Could not complete Google sign-in. Please try again.")
        return redirect(url_for("login"))

    userinfo = fetch_google_userinfo(token_data["access_token"])
    if not userinfo:
        set_auth_notice(error="Could not fetch your Google profile. Please try again.")
        return redirect(url_for("login"))

    email = (userinfo.get("email") or "").strip().lower()
    if not email:
        set_auth_notice(error="Google account email was unavailable.")
        return redirect(url_for("login"))

    name = (userinfo.get("name") or userinfo.get("given_name") or "Google User").strip()
    profile_photo = (userinfo.get("picture") or "").strip()
    google_id = (userinfo.get("sub") or "").strip()

    user = db.session.query(User).filter_by(email=email).first()
    if user is None:
        user = User(
            name=name,
            email=email,
            password="",
            is_verified=True,
            joined_date=date.today().strftime("%B %d, %Y"),
        )
        set_password(user, random_password())
        user.profile_photo = profile_photo
        user.google_id = google_id
        db.session.add(user)
    else:
        user.is_verified = True
        user.name = name or user.name
        if profile_photo:
            user.profile_photo = profile_photo
        if google_id:
            user.google_id = google_id
        if not password_is_hashed(user.password):
            set_password(user, random_password())

    db.session.commit()
    login_user(user)
    return redirect(url_for("get_all_posts"))


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

        new_user = User(name=name, email=mail, password="", joined_date=date.today().strftime("%B %d, %Y"))
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
    sync_post_publication_state(requested_post)
    if not is_post_visible(requested_post, include_unpublished=is_admin_user(current_user)):
        return abort(404)

    comment_sort = (request.args.get("comment_sort") or "relevant").strip().lower()
    if comment_sort not in {"relevant", "newest", "oldest"}:
        comment_sort = "relevant"

    if request.method == 'GET':
        requested_post.views = (requested_post.views or 0) + 1
        db.session.commit()
        comments = sorted_comments_for_post(post_id, comment_sort)
        form = CommentForm()
        current_reaction = post_user_reaction(requested_post, current_user)
        bookmarked = post_is_bookmarked(requested_post, current_user)
        return render_template(
            "post.html",
            post=requested_post,
            user_id=current_user.get_id(),
            form=form,
            comments=comments,
            suggested_posts=suggested_posts_for(requested_post),
            current_reaction=current_reaction,
            is_bookmarked=bookmarked,
            comment_sort=comment_sort,
            is_logged_in=is_logged_in(),
        )
    else:
        if not current_user.is_authenticated:
            if wants_json_response():
                return jsonify({"message": "Please log in to post comments."}), 401
            error = "Please log in to post comments."
            return render_template("login.html", error=error, form=LoginForm())
        comment_text = request.form.get("comment")
        is_allowed, moderation_reason = moderate_comment(comment_text, requested_post.title)
        author = db.session.query(User).filter_by(id=current_user.id).first()
        new_comment = Comments(
            text=comment_text,
            author=author,
            parent_post_id=post_id,
            created_at=to_timestamp(utc_now()),
            status="approved" if is_allowed else "rejected",
            moderation_reason=moderation_reason,
        )
        db.session.add(new_comment)
        db.session.commit()
        if wants_json_response():
            if not is_allowed:
                return jsonify({
                    "message": f"Your comment was held back by moderation. {moderation_reason}",
                }), 422
            approved_count = db.session.query(Comments).filter_by(parent_post_id=post_id, status="approved").count()
            return jsonify({
                "commentHtml": render_template("_comment_card.html", x=new_comment),
                "commentCount": approved_count,
                "createdAt": new_comment.created_label,
            })
        if not is_allowed:
            flash(f"Your comment was held back by moderation. {moderation_reason}", "danger")
        return redirect(url_for("show_post", post_id=post_id, comment_sort=comment_sort))


@app.route("/about")
def about():
    return render_template("about.html", is_logged_in=is_logged_in())


@app.route("/contact")
def contact():
    return render_template("contact.html", is_logged_in=is_logged_in())


@app.route("/post/<int:post_id>/react/<string:action>", methods=["POST"])
def react_post(post_id, action):
    if not current_user.is_authenticated:
        if wants_json_response():
            return jsonify({"message": "Please log in to react to posts."}), 401
        flash("Please log in to react to posts.", "danger")
        return redirect(url_for("login"))

    post = BlogPost.query.get(post_id)
    if post is None or not is_post_visible(post, include_unpublished=is_admin_user(current_user)):
        return abort(404)

    value = 1 if action == "like" else -1 if action == "dislike" else 0
    if value == 0:
        return abort(400)

    reaction = PostReaction.query.filter_by(post_id=post.id, user_id=current_user.id).first()
    if reaction and reaction.value == value:
        db.session.delete(reaction)
    else:
        if reaction is None:
            reaction = PostReaction(
                post_id=post.id,
                user_id=current_user.id,
                created_at=to_timestamp(utc_now()),
            )
            db.session.add(reaction)
        reaction.value = value
    db.session.commit()
    if wants_json_response():
        return jsonify(post_engagement_payload(post, current_user))
    return redirect(url_for("show_post", post_id=post.id))


@app.route("/post/<int:post_id>/bookmark", methods=["POST"])
def bookmark_post(post_id):
    if not current_user.is_authenticated:
        if wants_json_response():
            return jsonify({"message": "Please log in to save posts."}), 401
        flash("Please log in to save posts.", "danger")
        return redirect(url_for("login"))

    post = BlogPost.query.get(post_id)
    if post is None or not is_post_visible(post, include_unpublished=is_admin_user(current_user)):
        return abort(404)

    bookmark = Bookmark.query.filter_by(post_id=post.id, user_id=current_user.id).first()
    if bookmark is None:
        bookmark = Bookmark(post_id=post.id, user_id=current_user.id, saved_at=to_timestamp(utc_now()))
        db.session.add(bookmark)
    else:
        db.session.delete(bookmark)
    db.session.commit()
    if wants_json_response():
        return jsonify(post_engagement_payload(post, current_user))
    return redirect(url_for("show_post", post_id=post.id))


@app.route("/saved-articles")
def saved_articles():
    if not current_user.is_authenticated:
        flash("Please log in to view saved articles.", "danger")
        return redirect(url_for("login"))

    bookmarks = Bookmark.query.filter_by(user_id=current_user.id).order_by(Bookmark.id.desc()).all()
    saved_posts = [bookmark.post for bookmark in bookmarks if bookmark.post and is_post_visible(bookmark.post, include_unpublished=is_admin_user(current_user))]
    return render_template("saved_articles.html", saved_posts=saved_posts, is_logged_in=True)


@app.route("/profile", methods=["GET", "POST"])
def profile():
    if not current_user.is_authenticated:
        flash("Please log in to view your profile.", "danger")
        return redirect(url_for("login"))

    profile_user = User.query.get(current_user.id)
    if request.method == "POST":
        profile_user.profile_photo = (request.form.get("profile_photo") or "").strip()
        profile_user.bio = (request.form.get("bio") or "").strip()
        profile_user.github_url = (request.form.get("github_url") or "").strip()
        profile_user.twitter_url = (request.form.get("twitter_url") or "").strip()
        profile_user.website_url = (request.form.get("website_url") or "").strip()
        db.session.commit()
        flash("Your profile has been updated.", "success")
        return redirect(url_for("profile"))

    saved_posts = [bookmark.post for bookmark in profile_user.bookmarks if bookmark.post and is_post_visible(bookmark.post, include_unpublished=is_admin_user(current_user))]
    authored_comments = Comments.query.filter_by(author_id=profile_user.name, status="approved").order_by(Comments.id.desc()).all()
    return render_template(
        "profile.html",
        profile_user=profile_user,
        saved_posts=saved_posts,
        authored_comments=authored_comments,
        body_class="plain-page",
        is_logged_in=True,
    )


def admin_only(f):
    @wraps(f)
    def wrapper_func(*args, **kwargs):
        if not is_admin_user(current_user):
            return abort(403)
        return f(*args, **kwargs)
    return wrapper_func


@app.route("/admin/dashboard")
@admin_only
def admin_dashboard():
    sync_scheduled_posts()
    posts = BlogPost.query.order_by(BlogPost.id.desc()).all()
    visible = [post for post in posts if is_post_visible(post, include_unpublished=False)]
    scheduled_posts = [post for post in posts if parse_timestamp(post.published_at) is None and parse_timestamp(post.scheduled_for)]
    total_posts = len(posts)
    total_users = User.query.count()
    total_comments = Comments.query.filter_by(status="approved").count()
    tagged_posts = BlogPost.query.filter(BlogPost.tags != "").count()
    total_views = sum(post.views or 0 for post in posts)
    total_reactions = sum(post.like_count + post.dislike_count for post in posts)
    total_bookmarks = sum(post.bookmark_count for post in posts)
    engagement_total = total_comments + total_reactions + total_bookmarks
    most_viewed_post = max(visible, key=lambda post: post.views or 0, default=None)
    traffic_value = f"{total_views} visits"

    recent_posts = posts[:5]
    recent_comments = Comments.query.filter_by(status="approved").order_by(Comments.id.desc()).limit(6).all()
    newest_users = User.query.order_by(User.id.desc()).limit(5).all()
    newsletter_count = NewsletterSubscription.query.count()

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
            "label": "Traffic",
            "value": total_views,
            "detail": "Total article views across the site",
        },
        {
            "label": "Engagement",
            "value": engagement_total,
            "detail": "Likes, dislikes, bookmarks, and comments",
        },
    ]
    top_viewed_posts = sorted(visible, key=lambda post: (post.views or 0, post.id), reverse=True)[:5]
    top_engaged_posts = sorted(visible, key=lambda post: (post.engagement_score, post.id), reverse=True)[:5]
    max_views = max([post.views or 0 for post in top_viewed_posts] or [1])
    max_engagement = max([post.engagement_score for post in top_engaged_posts] or [1])
    dashboard_charts = {
        "views": [
            {
                "label": post.title,
                "value": post.views or 0,
                "width": max(6, int(((post.views or 0) / max_views) * 100)) if max_views else 6,
            }
            for post in top_viewed_posts
        ],
        "engagement": [
            {
                "label": post.title,
                "value": post.engagement_score,
                "width": max(6, int((post.engagement_score / max_engagement) * 100)) if max_engagement else 6,
            }
            for post in top_engaged_posts
        ],
    }

    return render_template(
        "dashboard.html",
        dashboard_stats=dashboard_stats,
        dashboard_charts=dashboard_charts,
        recent_posts=recent_posts,
        recent_comments=recent_comments,
        newest_users=newest_users,
        most_viewed_post=most_viewed_post,
        total_comments=total_comments,
        tagged_posts=tagged_posts,
        traffic_value=traffic_value,
        scheduled_posts=scheduled_posts,
        newsletter_count=newsletter_count,
        body_class="plain-page",
        is_logged_in=True,
    )


@app.route("/new-post", methods=["GET", 'POST'])
@admin_only
def add_new_post():
    form = CreatePostForm()
    ai_message = None

    if request.method == "POST" and form.generate_draft.data:
        form.use_ai.data = True
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
        form.use_ai.data = True
        generated_title, summary, tags, ai_message = generate_ai_content(
            form.title.data,
            form.subtitle.data,
            form.body.data,
        )
        form.title.data = generated_title
        form.summary.data = summary
        form.tags.data = tags
        return render_template("make-post.html", form=form, is_logged_in=True, is_edit=False, ai_message=ai_message)

    if request.method == "POST" and form.export_pdf.data:
        return make_article_pdf_response(
            title=form.title.data,
            subtitle=form.subtitle.data,
            author_name=current_user.name,
            published_date=(parse_datetime_local(form.scheduled_for.data) or utc_now()).strftime("%B %d, %Y"),
            body=form.body.data,
            summary=(form.summary.data or "").strip(),
            tags=normalize_tags(form.tags.data),
        )

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
        cover_image = resolve_cover_image(form)
        created_at = utc_now()
        scheduled_for = parse_datetime_local(form.scheduled_for.data)
        published_at = scheduled_for if scheduled_for and scheduled_for > utc_now() else created_at
        new_post = BlogPost(
            title=form.title.data,
            subtitle=form.subtitle.data,
            body=form.body.data,
            img_url=cover_image,
            author=db.session.query(User).filter_by(id=current_user.id).first(),
            date=(scheduled_for or created_at).strftime("%B %d, %Y"),
            summary=summary,
            tags=tags,
            created_at=to_timestamp(created_at),
            published_at="" if scheduled_for and scheduled_for > utc_now() else to_timestamp(published_at),
            scheduled_for=to_timestamp(scheduled_for) if scheduled_for else "",
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
        use_ai=False,
        topic=post.title,
        title=post.title,
        subtitle=post.subtitle,
        scheduled_for=(parse_timestamp(post.scheduled_for).strftime("%Y-%m-%dT%H:%M") if parse_timestamp(post.scheduled_for) else ""),
        img_url=post.img_url,
        body=post.body,
        summary=post.summary,
        tags=post.tags,
    )
    ai_message = None

    if request.method == "POST" and edit_form.generate_draft.data:
        edit_form.use_ai.data = True
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
        edit_form.use_ai.data = True
        generated_title, summary, tags, ai_message = generate_ai_content(
            edit_form.title.data,
            edit_form.subtitle.data,
            edit_form.body.data,
        )
        edit_form.title.data = generated_title
        edit_form.summary.data = summary
        edit_form.tags.data = tags
        return render_template("make-post.html", form=edit_form, is_logged_in=True, is_edit=True, ai_message=ai_message)

    if request.method == "POST" and edit_form.export_pdf.data:
        return make_article_pdf_response(
            title=edit_form.title.data,
            subtitle=edit_form.subtitle.data,
            author_name=post.author.name,
            published_date=(parse_datetime_local(edit_form.scheduled_for.data) or parse_timestamp(post.published_at) or utc_now()).strftime("%B %d, %Y"),
            body=edit_form.body.data,
            summary=(edit_form.summary.data or "").strip(),
            tags=normalize_tags(edit_form.tags.data),
        )

    if edit_form.validate_on_submit():
        scheduled_for = parse_datetime_local(edit_form.scheduled_for.data)
        post.title = edit_form.title.data
        post.subtitle = edit_form.subtitle.data
        post.img_url = resolve_cover_image(edit_form)
        post.body = edit_form.body.data
        post.summary = (edit_form.summary.data or "").strip()
        post.tags = normalize_tags(edit_form.tags.data)
        post.scheduled_for = to_timestamp(scheduled_for) if scheduled_for else ""
        if scheduled_for and scheduled_for > utc_now():
            post.published_at = ""
            post.date = scheduled_for.strftime("%B %d, %Y")
        else:
            publication_time = scheduled_for or parse_timestamp(post.published_at) or utc_now()
            post.published_at = to_timestamp(publication_time)
            post.date = publication_time.strftime("%B %d, %Y")
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


@app.route("/post/<int:post_id>/export")
def export_post_pdf(post_id):
    post = BlogPost.query.get(post_id)
    if post is None or not is_post_visible(post, include_unpublished=is_admin_user(current_user)):
        return abort(404)
    return make_article_pdf_response(
        title=post.title,
        subtitle=post.subtitle,
        author_name=post.author.name,
        published_date=post.date,
        body=post.body,
        summary=post.summary,
        tags=post.tags,
    )


@app.route("/delete/<int:post_id>")
@admin_only
def delete_post(post_id):
    post_to_delete = BlogPost.query.get(post_id)
    db.session.delete(post_to_delete)
    db.session.commit()
    return redirect(url_for('get_all_posts', is_logged_in=True))


if __name__ == "__main__":
    app.run(debug=True)
