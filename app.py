from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
from livereload import Server
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "ai-lesson-secret-key"

app.debug = True
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

def get_db():
    conn = sqlite3.connect("users.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    errors = {}

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not name:
            errors["name"] = "กรุณากรอกชื่อ"

        if not email or "@" not in email or "." not in email:
            errors["email"] = "อีเมลไม่ถูกต้อง"

        if len(password) < 8:
            errors["password"] = "รหัสผ่านต้องมีอย่างน้อย 8 ตัวอักษร"

        if not errors:
            hashed_password = generate_password_hash(password)
            conn = get_db()
            try:
                conn.execute(
                    "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                    (name, email, hashed_password)
                )
                conn.commit()
                conn.close()
                return redirect(url_for("login", registered="1"))

            except sqlite3.IntegrityError:
                conn.close()
                errors["email"] = "อีเมลนี้ถูกใช้งานแล้ว"

        return render_template("register.html", errors=errors,
                                name=name, email=email)

    return render_template("register.html", errors=errors)


@app.route("/login", methods=["GET", "POST"])
def login():
    not_registered = False
    password_error = None

    just_registered = request.args.get("registered") == "1"

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        conn.close()

        if not user:
            not_registered = True

        elif not check_password_hash(user["password"], password):
            password_error = "รหัสผ่านไม่ถูกต้อง"

        else:
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            return render_template("login.html", login_success=True)

    return render_template(
        "login.html",
        not_registered=not_registered,
        password_error=password_error,
        just_registered=just_registered
    )


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("dashboard.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    init_db()

    server = Server(app)
    server.watch("templates/*.html")
    server.watch("static/*.css")

    server.serve(
        host="127.0.0.1",
        port=5000,
        debug=True,
        live_css=True
    )