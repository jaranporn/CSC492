from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
from livereload import Server
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

app.secret_key = "ai-lesson-secret-key"


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

    if request.method == "POST":

        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]

        hashed_password = generate_password_hash(password)

        conn = get_db()

        try:
            conn.execute(
                "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                (name, email, hashed_password)
            )

            conn.commit()
            conn.close()

            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            conn.close()
            return "อีเมลนี้ถูกใช้งานแล้ว"

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form["email"]
        password = request.form["password"]

        conn = get_db()

        user = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        conn.close()

        if user and check_password_hash(user["password"], password):

            session["user_id"] = user["id"]
            session["user_name"] = user["name"]

            return redirect(url_for("dashboard"))

        return "อีเมลหรือรหัสผ่านไม่ถูกต้อง"

    return render_template("login.html")


@app.route("/dashboard")
def dashboard():

    if "user_id" not in session:
        return redirect(url_for("login"))

    return f"""
    <h1>สวัสดี {session["user_name"]}</h1>
    <p>เข้าสู่ระบบสำเร็จแล้ว</p>
    <a href="/logout">ออกจากระบบ</a>
    """

@app.route("/logout")

def logout():

    session.clear()

    return redirect(url_for("login"))

if __name__ == "__main__":
    server = Server(app)

    server.watch("templates", delay=0.5)
    server.watch("static", delay=0.5)

    server.serve(
        host="127.0.0.1",
        port=5000,
        debug=False,
        live_css=True
    )