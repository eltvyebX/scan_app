import os
import shutil
import sqlite3
from datetime import datetime
from fastapi import FastAPI, Request, Form, File, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageFilter
import pytesseract
import tempfile
import re

app = FastAPI()

DB_NAME = "bank_receipts.db"

# --------------------------------------------------
# Templates & Static Files
# --------------------------------------------------
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --------------------------------------------------
# Initialize DB
# --------------------------------------------------
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        # Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE,
                bank_account TEXT,
                pin TEXT
            )
        """)
        # Transactions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                image_path TEXT,
                amount REAL,
                created_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        conn.commit()

init_db()

# --------------------------------------------------
# OCR Function to extract amount from image
# --------------------------------------------------
def extract_amount_from_image(image_path: str) -> float:
    try:
        img = Image.open(image_path)
        img = img.convert("L")
        img = img.filter(ImageFilter.SHARPEN)
        width, height = img.size
        img = img.resize((int(width*1.5), int(height*1.5)), Image.LANCZOS)

        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp_file:
            tmp_filename = tmp_file.name
            img.save(tmp_filename, format="TIFF")

        text = pytesseract.image_to_string(tmp_filename, lang="ara+eng")
        os.remove(tmp_filename)

        # البحث عن كلمة "المبلغ" ثم الرقم بعدها
        match = re.search(r'المبلغ[\s:.]*([\d.,]+)', text)
        if match:
            amount_str = match.group(1).replace(",", "").strip()
            return float(amount_str)
        return 0.0
    except Exception as e:
        print("OCR Error:", e)
        return 0.0

# --------------------------------------------------
# Helper: calculate total amount for a user
# --------------------------------------------------
def get_total_amount(user_id: int) -> float:
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(amount) FROM transactions WHERE user_id = ?", (user_id,))
        total = cursor.fetchone()[0]
        return total if total else 0.0

# --------------------------------------------------
# Routes
# --------------------------------------------------
@app.get("/")
def start_page(request: Request):
    return templates.TemplateResponse("start_page.html", {"request": request})

@app.get("/register")
def show_register(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register")
def register_user(request: Request, user_id: str = Form(...), bank_account: str = Form(...), pin: str = Form(...)):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (user_id, bank_account, pin) VALUES (?, ?, ?)", (user_id, bank_account, pin))
        conn.commit()
        cursor.execute("SELECT id FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
    return templates.TemplateResponse("show_pin.html", {"request": request, "pin": pin, "user_id": user[0]})

@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
def login_user(request: Request, bank_account: str = Form(...), pin: str = Form(...)):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE bank_account = ? AND pin = ?", (bank_account, pin))
        user = cursor.fetchone()
    if user:
        response = RedirectResponse(url="/index", status_code=303)
        one_month = 30*24*60*60
        response.set_cookie("current_user", str(user[0]), max_age=one_month)
        return response
    return templates.TemplateResponse("login.html", {"request": request, "error": "بيانات الدخول غير صحيحة"})

# --------------------------------------------------
# Index Page: view & upload transactions
# --------------------------------------------------
@app.get("/index")
def index(request: Request):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/login", status_code=303)
    try:
        user_id = int(user_id_str)
    except:
        return RedirectResponse(url="/login", status_code=303)

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC", (user_id,))
        transactions = cursor.fetchall()
    
    total_amount = get_total_amount(user_id)
    
    return templates.TemplateResponse("index.html", {"request": request, "transactions": transactions, "total_amount": total_amount})

# --------------------------------------------------
# Upload Image & extract amount
# --------------------------------------------------
@app.post("/upload")
async def upload_receipt(request: Request, file: UploadFile = File(...)):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/login", status_code=303)
    try:
        user_id = int(user_id_str)
    except:
        return RedirectResponse(url="/login", status_code=303)

    if not os.path.exists("uploads"):
        os.makedirs("uploads")

    file_path = os.path.join("uploads", file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    amount = extract_amount_from_image(file_path)
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO transactions (user_id, image_path, amount, created_at) VALUES (?, ?, ?, ?)",
            (user_id, file_path, amount, created_at)
        )
        conn.commit()

    return RedirectResponse(url="/index", status_code=303)

# --------------------------------------------------
# Delete transaction
# --------------------------------------------------
@app.post("/delete/{id}")
def delete_transaction(id: int, request: Request):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/login", status_code=303)
    try:
        user_id = int(user_id_str)
    except:
        return RedirectResponse(url="/login", status_code=303)

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (id, user_id))
        conn.commit()

    return RedirectResponse(url="/index", status_code=303)
