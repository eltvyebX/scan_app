import os
import sqlite3
from datetime import datetime
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from pathlib import Path
import shutil

# --------------------------------------------------
# FastAPI + DB Setup
# --------------------------------------------------
app = FastAPI()
DB_NAME = "bank_receipts.db"
UPLOAD_DIR = "static/uploads"

Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)  # إنشاء مجلد الصور إذا لم يكن موجودًا

def init_db():
    """تهيئة قاعدة البيانات وإنشاء الجداول إذا لم تكن موجودة."""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        # users table
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE,
                bank_account TEXT,
                pin TEXT
            )
        """)
        # transactions table مع image_path
        c.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                trx_last4 TEXT,
                trx_date TEXT,
                amount REAL,
                image_path TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        conn.commit()

init_db()

# static
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ---------------------------
# 🏠 Start Page
# ---------------------------
@app.get("/")
def start_page(request: Request):
    return templates.TemplateResponse("start_page.html", {"request": request})

# ---------------------------
# 📝 تسجيل مستخدم جديد
# ---------------------------
@app.get("/register")
def show_register(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register")
def register_user(request: Request,
                  user_id: str = Form(...),
                  bank_account: str = Form(...),
                  pin: str = Form(...)):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO users (user_id, bank_account, pin) VALUES (?, ?, ?)",
                  (user_id, bank_account, pin))
        conn.commit()
        c.execute("SELECT id FROM users WHERE user_id = ?", (user_id,))
        db_user = c.fetchone()

    return templates.TemplateResponse(
        "show_pin.html",
        {"request": request, "pin": pin, "user_id": db_user[0]}
    )

# ---------------------------
# 🔐 صفحة تسجيل الدخول
# ---------------------------
@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
def login_user(request: Request,
               bank_account: str = Form(...),
               pin: str = Form(...)):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE bank_account=? AND pin=?", (bank_account, pin))
        user = c.fetchone()

    if user:
        response = RedirectResponse(url="/index", status_code=303)
        one_month = 30 * 24 * 60 * 60
        response.set_cookie("current_user", str(user[0]), max_age=one_month)
        return response
    else:
        return templates.TemplateResponse("login.html",
                                          {"request": request, "error": "بيانات الدخول غير صحيحة"})

# ---------------------------
# 🧾 صفحة العمليات (Index)
# ---------------------------
@app.get("/index")
def index(request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse(url="/", status_code=303)
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "data": {"trx_last4": "", "date_time": current_time, "amount": 0.0}}
    )

# ---------------------------
# 💾 حفظ عملية جديدة مع الصورة
# ---------------------------
@app.post("/confirm")
async def confirm_data(request: Request,
                       trx_last4: str = Form(...),
                       amount: float = Form(...),
                       receipt_image: UploadFile = File(...)):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/", status_code=303)
    try:
        user_id = int(user_id_str)
    except ValueError:
        return RedirectResponse(url="/", status_code=303)

    date_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # حفظ الصورة في المجلد
    filename = f"{user_id}_{int(datetime.now().timestamp())}_{receipt_image.filename}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(receipt_image.file, buffer)
    
    image_path = f"/{file_path.replace(os.sep, '/')}"  # لينك للوصول عبر الويب

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO transactions (user_id, trx_last4, trx_date, amount, image_path) VALUES (?, ?, ?, ?, ?)",
            (user_id, trx_last4, date_time, amount, image_path)
        )
        conn.commit()

    return RedirectResponse(url="/transactions", status_code=303)

# ---------------------------
# 📊 صفحة عرض الإشعارات
# ---------------------------
@app.get("/transactions")
def view_transactions(request: Request):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/", status_code=303)
    try:
        user_id_int = int(user_id_str)
    except ValueError:
        return RedirectResponse(url="/", status_code=303)

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC", (user_id_int,))
        trs = cursor.fetchall()

    total = sum([float(t["amount"]) for t in trs]) if trs else 0
    return templates.TemplateResponse("view.html",
                                      {"request": request, "transactions": trs, "total_amount": total})

# ---------------------------
# 🗑️ حذف إشعار واحد
# ---------------------------
@app.post("/delete/{id}")
def delete_transaction(id: int, request: Request):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/", status_code=303)
    try:
        user_id_int = int(user_id_str)
    except ValueError:
        return RedirectResponse(url="/", status_code=303)

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        # حذف الصورة من المجلد
        cursor.execute("SELECT image_path FROM transactions WHERE id=? AND user_id=?", (id, user_id_int))
        row = cursor.fetchone()
        if row and row[0]:
            image_file = row[0].lstrip("/")
            if os.path.exists(image_file):
                os.remove(image_file)
        cursor.execute("DELETE FROM transactions WHERE id=? AND user_id=?", (id, user_id_int))
        conn.commit()
    return RedirectResponse(url="/transactions", status_code=303)

# ---------------------------
# 🗑️ حذف جميع الإشعارات
# ---------------------------
@app.post("/delete_all")
def delete_all_transactions(request: Request):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/", status_code=303)
    try:
        user_id_int = int(user_id_str)
    except ValueError:
        return RedirectResponse(url="/", status_code=303)

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        # حذف الصور
        cursor.execute("SELECT image_path FROM transactions WHERE user_id=?", (user_id_int,))
        rows = cursor.fetchall()
        for row in rows:
            if row[0]:
                img_file = row[0].lstrip("/")
                if os.path.exists(img_file):
                    os.remove(img_file)
        cursor.execute("DELETE FROM transactions WHERE user_id=?", (user_id_int,))
        conn.commit()
    return RedirectResponse(url="/transactions", status_code=303)

# ---------------------------
# 📄 تصدير جميع الإشعارات PDF
# ---------------------------
@app.get("/export-all-pdf")
def export_all_pdf(request: Request):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/", status_code=303)
    try:
        user_id_int = int(user_id_str)
    except ValueError:
        return RedirectResponse(url="/", status_code=303)

    pdf_file = "all_transactions.pdf"
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id ASC", (user_id_int,))
        transactions = cursor.fetchall()

    doc = SimpleDocTemplate(pdf_file, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()
    elements.append(Paragraph("Transactions Report", styles['Title']))
    elements.append(Spacer(1, 12))

    data = [["Last 4 Digit", "Date", "Amount"]]
    total_amount = 0
    for trx in transactions:
        data.append([trx["trx_last4"], trx["trx_date"], "%.2f" % trx["amount"]])
        total_amount += trx["amount"]
    data.append(["", "TOTAL", "%.2f" % total_amount])

    table = Table(data, colWidths=[120, 180, 100])
    style = TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.darkblue),
        ('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        ('BACKGROUND', (0,-1), (-1,-1), colors.lightgrey),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
    ])
    table.setStyle(style)
    elements.append(table)
    doc.build(elements)
    return FileResponse(pdf_file, media_type='application/pdf', filename=pdf_file)
