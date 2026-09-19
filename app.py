import os

from dotenv import load_dotenv
from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify)
from werkzeug.security import generate_password_hash, check_password_hash

from extract import extract_text

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-only-change-me")

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras
    IntegrityError = psycopg2.IntegrityError
    ID_COLUMN = "SERIAL PRIMARY KEY"
    PLACEHOLDER = "%s"
else:
    import sqlite3
    IntegrityError = sqlite3.IntegrityError
    ID_COLUMN = "INTEGER PRIMARY KEY AUTOINCREMENT"
    PLACEHOLDER = "?"


def get_db():
    if DATABASE_URL:
        return psycopg2.connect(
            DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    conn = sqlite3.connect("users.db")
    conn.row_factory = sqlite3.Row
    return conn


def run(sql, params=(), fetchone=False):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql.replace("?", PLACEHOLDER), params)
        row = cur.fetchone() if fetchone else None
        conn.commit()
        return row
    finally:
        conn.close()


def init_db():
    run(f"""
        CREATE TABLE IF NOT EXISTS users (
            id {ID_COLUMN},
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)


init_db()


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
            try:
                run("INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                    (name, email, hashed_password))
                return redirect(url_for("login", registered="1"))
            except IntegrityError:
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

        user = run("SELECT * FROM users WHERE email = ?", (email,), fetchone=True)

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


@app.route("/api/summarize", methods=["POST"])
def api_summarize():
    if "user_id" not in session:
        return jsonify(error="กรุณาเข้าสู่ระบบ"), 401

    text = request.form.get("text", "").strip()
    f = request.files.get("file")

    try:
        if f and f.filename:
            text = extract_text(f)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    if len(text) < 50:
        return jsonify(error="เนื้อหาสั้นเกินไป หรืออ่านข้อความจากไฟล์ไม่ได้"), 400

    text = text[:30000]

    try:
        from llm import summarize_long
        summary = summarize_long(text)
    except Exception:
        app.logger.exception("summarize failed")
        return jsonify(error="เรียก AI ไม่สำเร็จ ลองใหม่อีกครั้ง"), 502

    return jsonify(summary=summary)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__ == "__main__":
    from livereload import Server

    app.debug = True
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    server = Server(app)
    server.watch("templates/*.html")
    server.watch("static/*.css")

    server.serve(
        host="127.0.0.1",
        port=5000,
        debug=True,
        live_css=True
    )