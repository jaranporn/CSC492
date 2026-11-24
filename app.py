import json
import os
from datetime import datetime, timedelta

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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "users.db")


def get_db():
    print("DATABASE PATH:", DB_PATH)
    print("DATABASE_URL:", bool(DATABASE_URL))

    if DATABASE_URL:
        return psycopg2.connect(
            DATABASE_URL,
            cursor_factory=psycopg2.extras.RealDictCursor
        )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
    run(f"""
        CREATE TABLE IF NOT EXISTS quiz_history (
            id {ID_COLUMN},
            user_id INTEGER NOT NULL,
            total INTEGER NOT NULL,
            correct INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def insert(sql, params=()):
    """INSERT แล้วคืนค่า id ของแถวใหม่ (ใช้ได้ทั้ง SQLite และ Postgres)"""
    conn = get_db()
    try:
        cur = conn.cursor()
        if DATABASE_URL:
            cur.execute(sql.replace("?", PLACEHOLDER) + " RETURNING id", params)
            new_id = cur.fetchone()["id"]
        else:
            cur.execute(sql, params)
            new_id = cur.lastrowid
        conn.commit()
        return new_id
    finally:
        conn.close()


def migrate_db():
    """สร้างตารางใหม่ และเพิ่มคอลัมน์ให้ฐานข้อมูลเดิม (รันซ้ำได้ปลอดภัย)"""
    run(f"""
        CREATE TABLE IF NOT EXISTS summaries (
            id {ID_COLUMN},
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            source_name TEXT,
            summary TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    run(f"""
        CREATE TABLE IF NOT EXISTS quizzes (
            id {ID_COLUMN},
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            summary_id INTEGER REFERENCES summaries(id) ON DELETE SET NULL,
            difficulty TEXT,
            quiz_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for col, typ in (("quiz_id", "INTEGER"), ("answers_json", "TEXT")):
        try:
            run(f"ALTER TABLE quiz_history ADD COLUMN {col} {typ}")
        except Exception:
            pass  
    run(
        "CREATE INDEX IF NOT EXISTS idx_history_user "
        "ON quiz_history (user_id, created_at)"
    )
    run(
        "CREATE INDEX IF NOT EXISTS idx_summaries_user "
        "ON summaries (user_id, created_at)"
    )


def _to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _norm(value):
    return value.strip().lower() if isinstance(value, str) else ""


def public_question(q):
    """ตัดเฉลยและคำอธิบายออก ก่อนส่งข้อสอบให้เบราว์เซอร์"""
    out = {"type": q["type"], "question": q["question"]}
    if q["type"] == "mc":
        out["choices"] = q["choices"]
    return out


def make_title(summary, source_name=None):
    if source_name:
        return os.path.splitext(source_name)[0][:80]
    for line in summary.splitlines():
        t = line.strip().lstrip("#*- ").strip()
        if t:
            return t[:80]
    return "บทเรียนไม่มีชื่อ"


def fmt_th_time(raw_time):
    if not raw_time:
        return "-"
    if isinstance(raw_time, str):
        try:
            dt = datetime.fromisoformat(raw_time.split(".")[0])
        except ValueError:
            dt = datetime.strptime(raw_time.split(".")[0], "%Y-%m-%d %H:%M:%S")
    else:
        dt = raw_time
    return (dt + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")


def build_review(quiz_json, answers_json):
    """รวมโจทย์กับคำตอบที่ผู้ใช้เคยตอบ สำหรับแสดงในประวัติ"""
    try:
        quiz = json.loads(quiz_json or "[]")
    except (TypeError, ValueError):
        return []
    try:
        answers = json.loads(answers_json) if answers_json else None
    except (TypeError, ValueError):
        answers = None

    review = []
    for i, q in enumerate(quiz):
        item = {"question": q.get("question", ""), "status": None, "mine": "", "right": ""}
        if answers is not None:
            mine = answers[i] if i < len(answers) else None
            if q.get("type") == "fill":
                accepted = {_norm(q.get("answer"))}
                accepted.update(_norm(x) for x in q.get("accepted") or [])
                ok = _norm(mine) != "" and _norm(mine) in accepted
                item["mine"] = mine if _norm(mine) else "(ไม่ได้ตอบ)"
                item["right"] = q.get("answer", "")
            else:
                choices = q.get("choices") or []
                ok = (
                    isinstance(mine, int)
                    and not isinstance(mine, bool)
                    and mine == q.get("answer")
                )
                valid = isinstance(mine, int) and not isinstance(mine, bool) and 0 <= mine < len(choices)
                item["mine"] = choices[mine] if valid else "(ไม่ได้ตอบ)"
                ans = q.get("answer")
                item["right"] = choices[ans] if isinstance(ans, int) and 0 <= ans < len(choices) else ""
            item["status"] = "ok" if ok else "bad"
        review.append(item)
    return review


init_db()
migrate_db()


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
    loaded = None
    sid = request.args.get("summary", type=int)
    if sid:
        row = run(
            "SELECT id, summary FROM summaries WHERE id = ? AND user_id = ?",
            (sid, session["user_id"]),
            fetchone=True,
        )
        if row:
            loaded = {"id": row["id"], "summary": row["summary"]}
    return render_template("dashboard.html", loaded=loaded)


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

    source_name = f.filename if (f and f.filename) else None
    summary_id = None
    try:
        summary_id = insert(
            "INSERT INTO summaries (user_id, title, source_name, summary) "
            "VALUES (?, ?, ?, ?)",
            (
                session["user_id"],
                make_title(summary, source_name),
                source_name,
                summary,
            ),
        )
    except Exception:
        app.logger.exception("save summary failed")

    return jsonify(summary=summary, summary_id=summary_id)


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

    num_choices = _to_int(data.get("num_choices"), 4)
    num_choices = max(3, min(num_choices, 6))

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
            summary_text,
            n_mc=n_mc,
            n_fill=n_fill,
            difficulty=difficulty,
            n_choices=num_choices,
        )
    except Exception:
        app.logger.exception("generate_quiz failed")
        return jsonify(error="สร้างข้อสอบไม่สำเร็จ ลองใหม่อีกครั้ง"), 502

    quiz = []
    for item in raw.get("multiple_choice", []):
        choices = item.get("choices", [])
        answer = _to_int(item.get("answer_index", 0), 0)
        if (
            not isinstance(choices, list)
            or len(choices) != num_choices
            or not 0 <= answer < num_choices
        ):
            continue
        quiz.append(
            {
                "type": "mc",
                "question": item.get("question", ""),
                "choices": choices,
                "answer": answer,
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

    quiz_id = None
    try:
        summary_id = data.get("summary_id")
        owned = None
        if isinstance(summary_id, int):
            owned = run(
                "SELECT id FROM summaries WHERE id = ? AND user_id = ?",
                (summary_id, session["user_id"]),
                fetchone=True,
            )
        quiz_id = insert(
            "INSERT INTO quizzes (user_id, summary_id, difficulty, quiz_json) "
            "VALUES (?, ?, ?, ?)",
            (
                session["user_id"],
                owned["id"] if owned else None,
                difficulty,
                json.dumps(quiz, ensure_ascii=False),
            ),
        )
    except Exception:
        app.logger.exception("save quiz failed")
        return jsonify(error="บันทึกข้อสอบไม่สำเร็จ ลองใหม่อีกครั้ง"), 500

    return jsonify(quiz=[public_question(q) for q in quiz], quiz_id=quiz_id)


@app.route("/api/quiz/submit", methods=["POST"])
def api_quiz_submit():
    if "user_id" not in session:
        return jsonify(error="กรุณาเข้าสู่ระบบ"), 401

    data = request.get_json(silent=True) or {}
    quiz_id = data.get("quiz_id")
    answers = data.get("answers")

    if (
        not isinstance(quiz_id, int)
        or isinstance(quiz_id, bool)
        or not isinstance(answers, list)
    ):
        return jsonify(error="ข้อมูลไม่ถูกต้อง"), 400

    row = run(
        "SELECT quiz_json FROM quizzes WHERE id = ? AND user_id = ?",
        (quiz_id, session["user_id"]),
        fetchone=True,
    )
    if not row:
        return jsonify(error="ไม่พบข้อสอบชุดนี้ กรุณาสร้างแบบทดสอบใหม่"), 404

    quiz = json.loads(row["quiz_json"])
    if len(answers) != len(quiz):
        return jsonify(error="จำนวนคำตอบไม่ตรงกับข้อสอบ"), 400

    results = []
    correct = 0
    for q, mine in zip(quiz, answers):
        if q.get("type") == "fill":
            accepted = {_norm(q.get("answer"))}
            accepted.update(_norm(a) for a in q.get("accepted") or [])
            ok = _norm(mine) != "" and _norm(mine) in accepted
        else:
            ok = (
                isinstance(mine, int)
                and not isinstance(mine, bool)
                and mine == q.get("answer")
            )
        correct += 1 if ok else 0
        results.append(
            {
                "correct": ok,
                "answer": q.get("answer"),
                "explanation": q.get("explanation", ""),
            }
        )

    run(
        "INSERT INTO quiz_history (user_id, total, correct, quiz_id, answers_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            session["user_id"],
            len(quiz),
            correct,
            quiz_id,
            json.dumps(answers, ensure_ascii=False),
        ),
    )
    return jsonify(total=len(quiz), correct=correct, results=results)


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
        SELECT h.total, h.correct, h.created_at, h.answers_json, q.quiz_json
        FROM quiz_history h
        LEFT JOIN quizzes q ON q.id = h.quiz_id
        WHERE h.user_id = ? ORDER BY h.created_at DESC LIMIT 10
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

        raw_time = r["created_at"]
        if raw_time:
            if isinstance(raw_time, str):
                try:
                    dt = datetime.fromisoformat(raw_time.split(".")[0])
                except ValueError:
                    dt = datetime.strptime(raw_time.split(".")[0], "%Y-%m-%d %H:%M:%S")
            else:
                dt = raw_time
            th_time = dt + timedelta(hours=7)
            created_str = th_time.strftime("%Y-%m-%d %H:%M:%S")
        else:
            created_str = "-"

        review = build_review(r["quiz_json"], r["answers_json"])

        history.append(
            {
                "total": t,
                "correct": c,
                "pct": pct,
                "created_at": created_str,
                "review": review,
            }
        )

    lesson_rows = run(
        """
        SELECT id, title, source_name, summary, created_at FROM summaries
        WHERE user_id = ? ORDER BY created_at DESC LIMIT 10
    """,
        (session["user_id"],),
        fetchall=True,
    )
    lessons = [
        {
            "id": r["id"],
            "title": r["title"],
            "source_name": r["source_name"],
            "summary": r["summary"],
            "created_at": fmt_th_time(r["created_at"]),
        }
        for r in lesson_rows or []
    ]

    return render_template(
        "account.html",
        lessons=lessons,
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