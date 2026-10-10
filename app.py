import os

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
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
            DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor
        )
    conn = sqlite3.connect("users.db")
    conn.row_factory = sqlite3.Row
    return conn


def run(sql, params=(), fetchone=False, fetchall=False):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql.replace("?", PLACEHOLDER), params)
        if fetchall:
            rows = cur.fetchall()
            conn.commit()
            return rows
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
    # ---------- ใหม่: ตารางประวัติการทำแบบทดสอบ ----------
    run(f"""
        CREATE TABLE IF NOT EXISTS quiz_history (
            id {ID_COLUMN},
            user_id INTEGER NOT NULL,
            total INTEGER NOT NULL,
            correct INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # -------------------------------------------------------


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
                run(
                    "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                    (name, email, hashed_password),
                )
                return redirect(url_for("login", registered="1"))
            except IntegrityError:
                errors["email"] = "อีเมลนี้ถูกใช้งานแล้ว"

        return render_template("register.html", errors=errors, name=name, email=email)

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
        just_registered=just_registered,
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


@app.route("/api/quiz", methods=["POST"])
def api_quiz():
    if "user_id" not in session:
        return jsonify(error="กรุณาเข้าสู่ระบบ"), 401

    data = request.get_json(silent=True) or {}
    summary_text = data.get("summary", "").strip()

    if len(summary_text) < 20:
        return jsonify(error="ไม่มีบทสรุปให้สร้างข้อสอบ กรุณาสรุปเนื้อหาก่อน"), 400

    difficulty_map = {"easy": "ง่าย", "medium": "ปานกลาง", "hard": "ยาก"}
    difficulty = difficulty_map.get(data.get("difficulty"), "ปานกลาง")

    types = data.get("types") or {}
    want_mc = bool(types.get("mc", True))
    want_fill = bool(types.get("fill", False))
    if not want_mc and not want_fill:
        want_mc = True

    count = data.get("count")
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 5
    count = max(1, min(count, 20))

    if want_mc and want_fill:
        n_mc = count - count // 2
        n_fill = count // 2
    elif want_mc:
        n_mc, n_fill = count, 0
    else:
        n_mc, n_fill = 0, count

    try:
        from llm import generate_quiz

        raw = generate_quiz(
            summary_text, n_mc=n_mc, n_fill=n_fill, difficulty=difficulty
        )
    except Exception:
        app.logger.exception("generate_quiz failed")
        return jsonify(error="สร้างข้อสอบไม่สำเร็จ ลองใหม่อีกครั้ง"), 502

    quiz = []
    for item in raw.get("multiple_choice", []):
        quiz.append(
            {
                "type": "mc",
                "question": item.get("question", ""),
                "choices": item.get("choices", []),
                "answer": item.get("answer_index", 0),
                "explanation": item.get("explanation", ""),
            }
        )
    for item in raw.get("fill_blank", []):
        quiz.append(
            {
                "type": "fill",
                "question": item.get("question", ""),
                "answer": item.get("answer", ""),
                "accepted": item.get("accepted", []),
                "explanation": item.get("explanation", ""),
            }
        )

    if not quiz:
        return jsonify(error="สร้างข้อสอบไม่สำเร็จ ลองใหม่อีกครั้ง"), 502

    return jsonify(quiz=quiz)


@app.route("/api/quiz/save", methods=["POST"])
def api_quiz_save():
    if "user_id" not in session:
        return jsonify(error="กรุณาเข้าสู่ระบบ"), 401

    data = request.get_json(silent=True) or {}
    total = data.get("total")
    correct = data.get("correct")

    if (
        not isinstance(total, int)
        or not isinstance(correct, int)
        or total <= 0
        or correct < 0
        or correct > total
    ):
        return jsonify(error="ข้อมูลไม่ถูกต้อง"), 400

    run(
        "INSERT INTO quiz_history (user_id, total, correct) VALUES (?, ?, ?)",
        (session["user_id"], total, correct),
    )
    return jsonify(ok=True)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/quiz")
def quiz_page():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("quiz.html")


@app.route("/quiz/result")
def quiz_result():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("quiz_result.html")


@app.route("/quiz/stats")
def quiz_stats():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("quiz_stats.html")


@app.route("/account")
def account():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user = run(
        "SELECT name, email FROM users WHERE id = ?",
        (session["user_id"],),
        fetchone=True,
    )

    agg = run(
        """
        SELECT COUNT(*) as attempts,
               COALESCE(SUM(total), 0) as total_q,
               COALESCE(SUM(correct), 0) as total_correct
        FROM quiz_history WHERE user_id = ?
    """,
        (session["user_id"],),
        fetchone=True,
    )

    rows = run(
        """
        SELECT total, correct, created_at FROM quiz_history
        WHERE user_id = ? ORDER BY created_at DESC LIMIT 10
    """,
        (session["user_id"],),
        fetchall=True,
    )

    attempts = agg["attempts"] if agg else 0
    total_q = agg["total_q"] if agg else 0
    total_correct = agg["total_correct"] if agg else 0
    avg_pct = round(total_correct * 100 / total_q) if total_q else 0

    history = []
    for r in rows or []:
        t, c = r["total"], r["correct"]
        pct = round(c * 100 / t) if t else 0
        history.append(
            {"total": t, "correct": c, "pct": pct, "created_at": r["created_at"]}
        )

    return render_template(
        "account.html",
        user=user,
        attempts=attempts,
        avg_pct=avg_pct,
        total_q=total_q,
        history=history,
    )


if __name__ == "__main__":
    from livereload import Server

    app.debug = True
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    server = Server(app)
    server.watch("templates/*.html")
    server.watch("static/*.css")

    server.serve(host="127.0.0.1", port=5000, debug=True, live_css=True)
