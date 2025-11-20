import os
import re
import shutil
import sqlite3
from datetime import datetime

from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from google.generativeai import configure, GenerativeModel

# --------------------------------------------------
# Gemini API
# --------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("❌ GEMINI_API_KEY is missing — add it in Render Env Vars!")

configure(api_key=GEMINI_API_KEY)
gemini_model = GenerativeModel("gemini-2.0-flash")

# --------------------------------------------------
# FastAPI + DB
# --------------------------------------------------
app = FastAPI()
DB_NAME = "bank_receipts.db"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trx_last4 TEXT,
                trx_date TEXT,
                amount REAL
            )
        """)
        conn.commit()

init_db()

# Templates
if not os.path.exists("templates"):
    os.makedirs("templates")

templates = Jinja2Templates(directory="templates")

# --------------------------------------------------
# Gemini OCR Function
# --------------------------------------------------
def extract_data_from_image(image_path):
    data = {"trx_last4": "", "date_time": "", "amount": 0.0}
    clean_text = ""

    try:
        with open(image_path, "rb") as img:
            image_bytes = img.read()

        prompt = """
        Read the text from this bank receipt image.
        Extract:
        - amount
        - transaction id (last 4 digits only)
        - date and time
        Return ONLY raw text exactly as you see it (no explanation).
        """

        result = gemini_model.generate_content(
            [
                prompt,
                {"mime_type": "image/jpeg", "data": image_bytes}
            ]
        )

        clean_text = result.text.strip()
        print("----- GEMINI OCR RAW -----")
        print(clean_text)
        print("---------------------------")

        # --------------------------
        # Extract Amount
        # --------------------------
        amount_match = re.search(r'(?i)(?:المبلغ|إجمالي|Total|Amount)[\s:]*([\d,.\s]+)', clean_text)
        if amount_match:
            raw_amount = amount_match.group(1).replace(" ", "").replace(",", "")
            try:
                data["amount"] = float(raw_amount)
            except ValueError:
                pass

        # --------------------------
        # Extract Transaction ID
        # --------------------------
        trx_match = re.search(r'(?i)(?:رقم العملية|Ref|ID|Trx)[\s:]*([0-9]{4,})', clean_text)
        if trx_match:
            data["trx_last4"] = trx_match.group(1)[-4:]
        else:
            long_number_match = re.search(r'(\d{8,})', clean_text)
            if long_number_match:
                data["trx_last4"] = long_number_match.group(1)[-4:]

        # --------------------------
        # Extract Date
        # --------------------------
        date_match = re.search(r'(\d{2}[\/\-]\d{2}[\/\-]\d{4}(?:\s\d{2}:\d{2}(:\d{2})?)?)', clean_text)
        if date_match:
            raw_date = date_match.group(1)
            input_formats = [
                "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y %H:%M",
                "%d/%m/%Y",
                "%d-%m-%Y %H:%M:%S",
                "%d-%m-%Y %H:%M",
                "%d-%m-%Y"
            ]
            for fmt in input_formats:
                try:
                    dt = datetime.strptime(raw_date, fmt)
                    data["date_time"] = dt.strftime("%d-%m-%Y %H:%M:%S")
                    break
                except ValueError:
                    continue

    except Exception as e:
        print("Gemini OCR Error:", e)

    return data, clean_text

# --------------------------------------------------
# Routes
# --------------------------------------------------
@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/camera")
def camera_page(request: Request):
    return templates.TemplateResponse("camera.html", {"request": request})

@app.post("/scan")
async def scan_receipt(request: Request, file: UploadFile = File(...)):
    temp_file = f"temp_{file.filename}"

    with open(temp_file, "wb") as f:
        shutil.copyfileobj(file.file, f)

    extracted_data, raw_text = extract_data_from_image(temp_file)

    os.remove(temp_file)

    return templates.TemplateResponse("review.html", {
        "request": request,
        "data": extracted_data,
        "raw_text": raw_text
    })

@app.post("/confirm")
def confirm_data(
    request: Request,
    trx_last4: str = Form(...),
    date_time: str = Form(...),
    amount: float = Form(...)
):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO transactions (trx_last4, trx_date, amount) VALUES (?, ?, ?)",
            (trx_last4, date_time, amount)
        )
        conn.commit()

    return RedirectResponse(url="/transactions", status_code=303)

@app.get("/transactions")
def view_transactions(request: Request):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions ORDER BY id DESC")
        trs = cursor.fetchall()

    total = sum([t["amount"] for t in trs]) if trs else 0

    return templates.TemplateResponse("view.html", {
        "request": request,
        "transactions": trs,
        "total_amount": total
    })

@app.post("/delete/{id}")
def delete_transaction(id: int):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transactions WHERE id = ?", (id,))
        conn.commit()

    return RedirectResponse(url="/transactions", status_code=303)
