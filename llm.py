import os, json, re
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(
    api_key=os.getenv("TYPHOON_API_KEY"),
    base_url=os.getenv("TYPHOON_BASE_URL"),
)
MODEL = os.getenv("TYPHOON_MODEL")

def ask(system, user, max_tokens=3000, temperature=0.3):
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
    return choice.message.content

def summarize(text):
    system = "คุณเป็นผู้ช่วยสรุปบทเรียนภาษาไทย ตอบเป็นภาษาไทยเท่านั้น"
    user = (
        "สรุปเนื้อหาต่อไปนี้ให้กระชับ ตัดส่วนที่ซ้ำซ้อน "
        "คงใจความสำคัญไว้ จัดเป็นหัวข้อและข้อย่อย\n\n" + text
    )
    return ask(system, user)

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
    chunks = split_chunks(text)
    if len(chunks) == 1:
        return summarize(text)
    parts = [summarize(c) for c in chunks]
    return summarize("\n\n".join(parts))

def _parse_json(raw):
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    match = re.search(r"\{.*\}", raw, re.S)
    return json.loads(match.group(0))

def generate_quiz(summary, n_mc=3, n_fill=0, difficulty="ปานกลาง", retries=2):
    difficulty_desc = {
        "ง่าย": "ระดับง่าย เน้นคำถามตรงไปตรงมา ถามข้อเท็จจริงพื้นฐานที่ปรากฏตรงๆ ในบทสรุป",
        "ปานกลาง": "ระดับปานกลาง ต้องเข้าใจและเชื่อมโยงเนื้อหาในบทสรุป ไม่ใช่แค่จำคำต่อคำ",
        "ยาก": "ระดับยาก ต้องวิเคราะห์ เปรียบเทียบ หรือประยุกต์ใช้เนื้อหาในบทสรุป ห้ามถามตรงๆ แบบระดับง่าย",
    }.get(difficulty, "ระดับปานกลาง")

    parts, schema_parts = [], []

    if n_mc > 0:
        parts.append(f"ข้อสอบปรนัย 4 ตัวเลือก {n_mc} ข้อ")
        schema_parts.append(
            '  "multiple_choice": [\n'
            '    {"question": "...", "choices": ["ก", "ข", "ค", "ง"], '
            '"answer_index": 0, "explanation": "อธิบายสั้นๆ ว่าทำไมข้อนี้ถูก"}\n'
            '  ]'
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
        "ตอบตามรูปแบบนี้เท่านั้น:\n"
        f"{schema}\n\n"
        f"บทสรุป:\n{summary}"
    )

    for _ in range(retries + 1):
        try:
            tokens_needed = min(6000, 1500 + (n_mc + n_fill) * 220)
            return _parse_json(ask(system, user, temperature=0.4, max_tokens=tokens_needed))
        except (json.JSONDecodeError, AttributeError):
            continue
    raise ValueError("โมเดลตอบ JSON ไม่ถูกรูปแบบ")