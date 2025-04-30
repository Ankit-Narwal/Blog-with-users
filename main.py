from datetime import date
from functools import wraps

from flask import Flask, render_template, redirect, url_for, request, abort
from flask_bootstrap import Bootstrap
from flask_ckeditor import CKEditor
from flask_gravatar import Gravatar
from flask_login import UserMixin, login_user, LoginManager, current_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import ForeignKey, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

from forms import CreatePostForm, RegisterForm, LoginForm, CommentForm

app = Flask(__name__)
app.config['SECRET_KEY'] = '8BYkEfBA6O6donzWlSihBXox7C0sKR6b'
ckeditor = CKEditor(app)
Bootstrap(app)
login_manager = LoginManager(app)
guvava = Gravatar(app)

# CONNECT TO DB
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///blog.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# CONFIGURE TABLES
Base = declarative_base()


class User(UserMixin, db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(250), nullable=False)
    email = db.Column(db.String(), unique=True, nullable=False)
    password = db.Column(db.String(30), nullable=False)
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
    comments = relationship("Comments", back_populates="parent_post")


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


db.create_all()


@app.route('/')
def get_all_posts():
    # id = current_user.get_id()
    # print(current_user.id)
    posts = BlogPost.query.all()
    if current_user.is_authenticated:
        return render_template("index.html", all_posts=posts)
    else:
        return render_template("index.html", all_posts=posts)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        form = RegisterForm()
        return render_template("register.html", form=form)
    else:
        name = request.form.get('name')
        mail = request.form.get('email')
        # print(db.session.query(User).filter_by(email=mail)
        if db.session.query(User).filter_by(email=mail).first():
            error = "You have already registered with this email. Kindly Log in Insted"
            return render_template("login.html", error=error, form=LoginForm())
        else:
            password = request.form.get('password')
            new_user = User(name=name, email=mail, password=password)
            db.session.add(new_user)
            db.session.commit()
            user = db.session.query(User).filter_by(email=mail).first()
            global user_id
            user_id = user.id
            login_user(user)
            posts = BlogPost.query.all()
            return render_template("index.html", is_logged_in=True, all_posts=posts, user_id=user_id)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        form = LoginForm()
        return render_template("login.html", form=form)
    else:
        email = request.form.get('email')
        user = db.session.query(User).filter_by(email=email).first()
        if user:
            if user.password == request.form.get("password"):
                global user_id
                user_id = user.id
                login_user(user)
                posts = BlogPost.query.all()
                return render_template("index.html", is_logged_in=True, all_posts=posts, user_id=user.id)
            else:
                error = "Sorry your password is wrong"
                return render_template("login.html", error=error, form=LoginForm())
        else:
            error = "Sorry this user does not exist in our database."
            return render_template("login.html", error=error, form=LoginForm())


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
    if request.method == 'GET':
        comments = db.session.query(Comments).all()
        requested_post = BlogPost.query.get(post_id)
        form = CommentForm()
        id = current_user.get_id()
        return render_template("post.html", post=requested_post, user_id=id, form=form, comments=comments,
                               is_logged_in=is_logged_in())
    else:
        if user_id < 1:
            form = LoginForm()
            error = "Please log in to post comments."
            return render_template("login.html", error=error, form=LoginForm())
        author = db.session.query(User).filter_by(id=user_id).first()
        new_comment = Comments(text=request.form.get("comment"), author=author, parent_post_id=post_id)
        db.session.add(new_comment)
        db.session.commit()
        form = CommentForm()
        id = current_user.get_id()
        requested_post = BlogPost.query.get(post_id)
        return render_template("post.html", post=requested_post, user_id=id, form=form, is_logged_in=is_logged_in())


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
        return f()
    return wrapper_func


@app.route("/new-post", methods=["GET", 'POST'])
@admin_only
def add_new_post():
    print(user_id)
    form = CreatePostForm()
    if form.validate_on_submit():
        new_post = BlogPost(
            title=form.title.data,
            subtitle=form.subtitle.data,
            body=form.body.data,
            img_url=form.img_url.data,
            author=db.session.query(User).filter_by(id=user_id).first(),
            date=date.today().strftime("%B %d, %Y")
        )
        db.session.add(new_post)
        db.session.commit()
        return redirect(url_for("get_all_posts"))
    return render_template("make-post.html", form=form, is_logged_in=True)


@app.route("/edit-post/<int:post_id>")
@admin_only
def edit_post(post_id):
    post = BlogPost.query.get(post_id)
    edit_form = CreatePostForm(
        title=post.title,
        subtitle=post.subtitle,
        img_url=post.img_url,
        author=post.author,
        body=post.body
    )
    if edit_form.validate_on_submit():
        post.title = edit_form.title.data
        post.subtitle = edit_form.subtitle.data
        post.img_url = edit_form.img_url.data
        post.author = edit_form.author.data
        post.body = edit_form.body.data
        db.session.commit()
        return redirect(url_for("show_post", post_id=post.id, is_logged_in=True))

    return render_template("make-post.html", form=edit_form, is_logged_in=True)


@app.route("/delete/<int:post_id>")
@admin_only
def delete_post(post_id):
    post_to_delete = BlogPost.query.get(post_id)
    db.session.delete(post_to_delete)
    db.session.commit()
    return redirect(url_for('get_all_posts', is_logged_in=True))


if __name__ == "__main__":
    app.run(debug=True)
