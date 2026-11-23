import os, json, re, time
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(
    api_key=os.getenv("TYPHOON_API_KEY"),
    base_url=os.getenv("TYPHOON_BASE_URL"),
)
MODEL = os.getenv("TYPHOON_MODEL")
MERGE_SUMMARY = os.getenv("SUMMARY_MERGE") == "1"
ADD_KEYWORDS = os.getenv("SUMMARY_KEYWORDS", "1") != "0"

def ask(system, user, max_tokens=3000, temperature=0.3):
    t0 = time.perf_counter()
    r = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    choice = r.choices[0]
    if choice.finish_reason == "length":
        print("WARNING: output truncated")
    usage = getattr(r, "usage", None)
    print(
        f"[llm] {time.perf_counter() - t0:.1f}s, "
        f"output tokens: {getattr(usage, 'completion_tokens', '?')}",
        flush=True,
    )
    return choice.message.content

def summarize(text):
    system = "คุณเป็นผู้ช่วยสรุปบทเรียนภาษาไทย ตอบเป็นภาษาไทยเท่านั้น"
    user = (
        "สรุปเนื้อหาต่อไปนี้ให้กระชับ ตัดส่วนที่ซ้ำซ้อน "
        "คงใจความสำคัญไว้ จัดเป็นหัวข้อและข้อย่อย\n\n" + text
    )
    return ask(system, user)

def summarize_part(text):
    system = "คุณเป็นผู้ช่วยสรุปบทเรียนภาษาไทย ตอบเป็นภาษาไทยเท่านั้น"
    user = (
        "ข้อความต่อไปนี้เป็นเพียงส่วนหนึ่งของบทเรียน สรุปให้กระชับ ตัดส่วนที่ซ้ำซ้อน "
        "คงใจความสำคัญไว้ จัดเป็นหัวข้อและข้อย่อย "
        "ใช้หัวข้อระดับ ### ไม่ต้องใส่เลขลำดับหน้าหัวข้อ "
        "ไม่ต้องมีชื่อเรื่องรวม คำนำ หรือบทสรุปท้าย\n\n" + text
    )
    return ask(system, user)

def _keyword_lines(raw):
    """เก็บเฉพาะบรรทัดที่เป็นรายการ (- / * / 1.) แล้วจัดให้เป็นรูปแบบ "- ..." เหมือนกันหมด"""
    lines = []
    for line in (raw or "").splitlines():
        m = re.match(r"^\s*(?:[-*\u2022]|\d+[.)])\s+(.*\S)\s*$", line)
        if m:
            lines.append("- " + m.group(1).strip())
    return lines

def _keyword_section(text):
    """ส่วน "คีย์เวิร์ดที่ควรจำ" ถ้าล้มเหลวหรือรูปแบบไม่ถูก คืนค่าว่าง เพื่อไม่ให้กระทบผลสรุปหลัก"""
    system = "คุณเป็นผู้ช่วยสรุปบทเรียนภาษาไทย ตอบเป็นภาษาไทย ยกเว้นศัพท์เทคนิคที่ให้คงเป็นภาษาอังกฤษ"
    user = (
        "จากเนื้อหาบทเรียนต่อไปนี้ เลือกคีย์เวิร์ดสำคัญที่ควรจำ 8-12 คำ "
        "ตอบเป็นรายการหัวข้อย่อยเท่านั้น แต่ละบรรทัดใช้รูปแบบ: "
        "- **คำศัพท์** — คำอธิบายสั้นๆ ภาษาไทยไม่เกิน 1 บรรทัด "
        "คงศัพท์เทคนิคเป็นภาษาอังกฤษตามต้นฉบับ "
        "ห้ามมีคำนำ ห้ามมีหัวข้อ ห้ามมีข้อความอื่นนอกจากรายการ\n\n" + text[:20000]
    )
    try:
        raw = ask(system, user, temperature=0.2, max_tokens=700)
    except Exception as e:
        print(f"[llm] keywords failed: {e}", flush=True)
        return ""
    lines = _keyword_lines(raw)[:12]
    if len(lines) < 3:
        return ""
    return "### 🔑 คีย์เวิร์ดที่ควรจำ\n\n" + "\n".join(lines)

def split_chunks(text, size=3000):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    chunks, cur = [], ""
    for line in lines:
        if cur and len(cur) + len(line) > size:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        chunks.append(cur)
    return chunks

def summarize_long(text):
    chunks = split_chunks(text, size=6000)
    workers = min(len(chunks) + 1, 6)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        kw = ex.submit(_keyword_section, text) if ADD_KEYWORDS else None
        if len(chunks) == 1:
            summary = summarize(text)
        elif MERGE_SUMMARY:
            parts = list(ex.map(summarize, chunks))
            summary = summarize("\n\n".join(parts))
        else:
            parts = list(ex.map(summarize_part, chunks))
            summary = "\n\n".join(part.strip() for part in parts)
        keywords = kw.result() if kw else ""
    return summary + ("\n\n" + keywords if keywords else "")

def _parse_json(raw):
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    match = re.search(r"\{.*\}", raw, re.S)
    return json.loads(match.group(0))

def generate_quiz(summary, n_mc=3, n_fill=0, difficulty="ปานกลาง", n_choices=4, retries=2):
    n_choices = max(3, min(int(n_choices), 6))
    difficulty_desc = {
        "ง่าย": "ระดับง่าย เน้นคำถามตรงไปตรงมา ถามข้อเท็จจริงพื้นฐานที่ปรากฏตรงๆ ในบทสรุป",
        "ปานกลาง": "ระดับปานกลาง ต้องเข้าใจและเชื่อมโยงเนื้อหาในบทสรุป ไม่ใช่แค่จำคำต่อคำ",
        "ยาก": "ระดับยาก ต้องวิเคราะห์ เปรียบเทียบ หรือประยุกต์ใช้เนื้อหาในบทสรุป ห้ามถามตรงๆ แบบระดับง่าย",
    }.get(difficulty, "ระดับปานกลาง")

    parts, schema_parts = [], []
    mc_rule = ""

    if n_mc > 0:
        parts.append(f"ข้อสอบปรนัย {n_choices} ตัวเลือก {n_mc} ข้อ")
        choices_example = ", ".join(['"..."'] * n_choices)
        schema_parts.append(
            '  "multiple_choice": [\n'
            f'    {{"question": "...", "choices": [{choices_example}], '
            f'"answer_index": 0, "explanation": "อธิบายสั้นๆ ว่าทำไมข้อนี้ถูก"}}\n'
            '  ]'
        )
        mc_rule = (
            f"ข้อปรนัยทุกข้อต้องมี choices ครบ {n_choices} ตัวเลือกพอดี "
            f"มีคำตอบที่ถูกเพียง 1 ตัวเลือก และ answer_index ต้องเป็นเลข 0 ถึง {n_choices - 1} "
            "ไม่ต้องใส่ ก. ข. ค. หรือ A. B. C. นำหน้าตัวเลือก\n"
        )

    if n_fill > 0:
        parts.append(f"ข้อเติมคำ {n_fill} ข้อ")
        schema_parts.append(
            '  "fill_blank": [\n'
            '    {"question": "ประโยคที่มี ____ เป็นช่องว่าง", "answer": "คำตอบ", '
            '"accepted": ["คำที่ยอมรับได้"], "explanation": "อธิบายสั้นๆ ว่าทำไมคำตอบนี้ถูก"}\n'
            '  ]'
        )

    schema = "{\n" + ",\n".join(schema_parts) + "\n}"

    system = "คุณเป็นผู้ออกข้อสอบภาษาไทย ตอบเป็น JSON เท่านั้น ห้ามมีข้อความอื่น"
    user = (
        f"จากบทสรุปต่อไปนี้ สร้าง{' และ'.join(parts)}\n"
        f"ระดับความยาก: {difficulty_desc}\n"
        f"{mc_rule}"
        "ตอบตามรูปแบบนี้เท่านั้น:\n"
        f"{schema}\n\n"
        f"บทสรุป:\n{summary}"
    )

    for _ in range(retries + 1):
        try:
            extra = max(0, n_choices - 4) * 30 * n_mc
            tokens_needed = min(6000, 1500 + (n_mc + n_fill) * 220 + extra)
            return _parse_json(ask(system, user, temperature=0.4, max_tokens=tokens_needed))
        except (json.JSONDecodeError, AttributeError):
            continue
    raise ValueError("โมเดลตอบ JSON ไม่ถูกรูปแบบ")