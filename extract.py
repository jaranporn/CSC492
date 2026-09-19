from pypdf import PdfReader

def extract_text(file_storage):
    name = file_storage.filename.lower()

    if name.endswith(".pdf"):
        reader = PdfReader(file_storage.stream)
        pages = [(p.extract_text() or "") for p in reader.pages]
        return "\n".join(pages).strip()

    if name.endswith(".txt"):
        raw = file_storage.read()
        for enc in ("utf-8-sig", "utf-8", "cp874"):
            try:
                return raw.decode(enc).strip()
            except UnicodeDecodeError:
                continue

    raise ValueError("รองรับเฉพาะไฟล์ PDF / TXT")