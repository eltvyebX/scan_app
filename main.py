import os
import re
import sqlite3
import base64
from datetime import datetime
from io import BytesIO

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from PIL import Image, ImageFilter, ImageOps
import numpy as np
import cv2
import easyocr

# -------------------------
# إعداد التطبيق وملفات
# -------------------------
app = FastAPI()
DB_NAME = "bank_receipts.db"
RECEIPTS_DIR = "receipts"
os.makedirs(RECEIPTS_DIR, exist_ok=True)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount(f"/{RECEIPTS_DIR}", StaticFiles(directory=RECEIPTS_DIR), name="receipts")

# -------------------------
# تهيئة DB
# -------------------------
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE,
                bank_account TEXT,
                pin TEXT
            )
        """)
        c.execute("""
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

# -------------------------
# تهيئة EasyOCR مرة واحدة (تحسين الأداء)
# -------------------------
# languages: arabic + english (حسب الحاجة)
try:
    reader = easyocr.Reader(['ar', 'en'], gpu=False)  # غيّر gpu=True إذا كنت تملك GPU
except Exception as e:
    # في حال فشل تحميل النموذج أثناء التطوير، ضع reader = None وتخطي OCR
    print("Warning: failed to initialize easyocr reader:", e)
    reader = None

# -------------------------
# دوال مساعدة لـ OCR
# -------------------------
AMOUNT_KEYWORDS = [
    r'المبلغ', r'الإجمالي', r'إجمالي', r'رصيد', r'المبلغ:', r'Amount', r'Total', r'Total:', r'Amount:'
]

# نمط قوي لالتقاط المبالغ: يدعم 1,234.56  أو 1 234.56 أو 1234,56 أو 1234.56 أو ١٢٣٤٫٥٦
NUMBER_PATTERN = r'(?:(?:\d{1,3}(?:[,\s\u00A0]\d{3})*(?:[.,]\d+)?))|(?:\d+(?:[.,]\d+)?)|(?:[٠-٩\.\،\٫\s]+)'

def normalize_number_str(s: str) -> str:
    # استبدال الأرقام العربية بالإنجليزية
    arabic_digits = '٠١٢٣٤٥٦٧٨٩'
    for i, d in enumerate(arabic_digits):
        s = s.replace(d, str(i))
    # استبدال الفواصل العربية والشرطيات الشاذة
    s = s.replace('\u066B', '.').replace('\u066C', ',')
    s = s.replace('٬', ',').replace('٫', '.').replace('،', ',')
    s = s.replace(' ', '').replace('\u00A0', '')
    # إذا وُجد أكثر من فاصل عشري (، و .) نعالج بافتراض أن آخر واحد هو الفاصل العشري
    if s.count('.') > 1 and s.count(',') == 0:
        # حذف النقاط الفاصلة آلاف، إبقاء آخر نقطة كفاصل عشري
        parts = s.split('.')
        s = ''.join(parts[:-1]) + '.' + parts[-1]
    if s.count(',') > 1 and s.count('.') == 0:
        parts = s.split(',')
        s = ''.join(parts[:-1]) + '.' + parts[-1]
    # تحويل أي فاصلة لعشرية إلى نقطة
    if ',' in s and '.' not in s:
        s = s.replace(',', '.')
    # إزالة أي أحرف غير رقم أو نقطة أو سالب
    s = re.sub(r'[^\d\.-]', '', s)
    return s

def preprocess_image_for_ocr(pil_image: Image.Image) -> np.ndarray:
    # تحويل للصيغ المناسبة لسهولة قراءة النصوص
    img = pil_image.convert('L')  # grayscale
    # تحسين التباين و الحدة
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.SHARPEN)
    # تكبير الصورة قليلاً لمساعدة OCR على الدقة
    w, h = img.size
    scale = 1.5 if max(w,h) < 2000 else 1.0
    if scale != 1.0:
        img = img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)
    # تحويل إلى numpy BGR (opencv)
    arr = np.array(img)
    return arr

def extract_amount_from_image_file(path: str) -> float:
    """يرجع مبلغ (float) المُستخرج أو 0.0 إن لم يُعثر."""
    global reader
    if reader is None:
        return 0.0

    try:
        pil = Image.open(path)
    except Exception as e:
        print("Cannot open image for OCR:", e)
        return 0.0

    img_np = preprocess_image_for_ocr(pil)

    # easyocr يعطي قائمة من (text, bbox, confidence)؛ في الإصدارات الحديثة طريقة الإخراج: result = reader.readtext(img)
    try:
        results = reader.readtext(img_np, detail=1, paragraph=False)
    except Exception as e:
        print("easyocr readtext error:", e)
        return 0.0

    # نجمع نصوص مضبوطة كسطور حسب موقع الـ bbox (y)
    # كل نتيجة: (bbox, text, conf) أو (bbox, text)
    lines = {}  # y_center -> [texts]
    for res in results:
        # تحقّق شكل النتيجة
        if len(res) == 3:
            bbox, text, conf = res
        elif len(res) == 2:
            bbox, text = res
            conf = 1.0
        else:
            continue

        # حساب مركز Y لعنصر النص
        ys = [p[1] for p in bbox]
        y_center = sum(ys) / len(ys)
        key = int(y_center // 10)  # تجميع كل العناصر القريبة
        lines.setdefault(key, []).append((y_center, text))

    # ترتيب الأسطر حسب Y، ثم دمج نصوص كل سطر حسب X (تقريب)
    ordered_lines = []
    for k in sorted(lines.keys()):
        parts = sorted(lines[k], key=lambda t: t[0])
        line_text = " ".join([p[1] for p in parts])
        ordered_lines.append(line_text)

    # 1) البحث عن سطر يحتوي كلمات المفتاحية ثم استخراج رقم في نفس السطر
    joined_text = "\n".join(ordered_lines)
    for line in ordered_lines:
        for kw in AMOUNT_KEYWORDS:
            if re.search(kw, line, flags=re.IGNORECASE):
                # البحث عن نمط الرقم في نفس السطر
                m = re.search(NUMBER_PATTERN, line)
                if m:
                    num_raw = m.group(0)
                    norm = normalize_number_str(num_raw)
                    try:
                        val = float(norm)
                        return round(val, 2)
                    except:
                        continue
    # 2) إذا لم نجد سطر به مفتاح: نبحث عن أي رقم كبير معقول في النص كله
    all_nums = re.findall(NUMBER_PATTERN, joined_text)
    candidates = []
    for n in all_nums:
        norm = normalize_number_str(n)
        if norm and re.search(r'\d', norm):
            try:
                v = float(norm)
                # استبعاد القيم الصغيرة جداً (مثل أرقام التعريف) إن كانت معقولة
                if v > 0:
                    candidates.append(v)
            except:
                continue
    # اختيار أكبر قيمة من المرشحين (غالباً المبلغ سيكون من أكبر الأرقام)
    if candidates:
        best = max(candidates)
        return round(best, 2)

    return 0.0

# -------------------------
# راوتس التطبيق
# -------------------------

@app.get("/")
def start_page(request: Request):
    return templates.TemplateResponse("start_page.html", {"request": request})

@app.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "user_id": ""})

@app.post("/register")
def register_user(request: Request, bank_account: str = Form(...), user_id: str = Form(...), pin: str = Form(...)):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO users (user_id, bank_account, pin) VALUES (?, ?, ?)", (user_id, bank_account, pin))
        conn.commit()
    # عرض صفحة show_pin مع البيانات
    return templates.TemplateResponse("show_pin.html", {"request": request, "pin": pin, "user_id": user_id})

@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
def login_user(request: Request, bank_account: str = Form(...), pin: str = Form(...)):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE bank_account=? AND pin=?", (bank_account, pin))
        row = c.fetchone()
    if row:
        response = RedirectResponse(url="/index", status_code=303)
        response.set_cookie(key="current_user", value=str(row[0]), max_age=30*24*60*60)
        return response
    else:
        return templates.TemplateResponse("login.html", {"request": request, "error": "بيانات الدخول غير صحيحة"})

@app.get("/index")
def index(request: Request):
    user_id = request.cookies.get("current_user")
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)
    # صفحة index يجب أن تحتوي زر للانتقال مباشرة إلى view — هذا في القالب index.html
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/upload_capture")
def upload_capture(request: Request, captured_image: str = Form(...)):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/login", status_code=303)
    try:
        user_id = int(user_id_str)
    except ValueError:
        return RedirectResponse(url="/login", status_code=303)

    # حفظ الصورة من base64
    try:
        header, encoded = captured_image.split(",", 1)
    except ValueError:
        encoded = captured_image
    image_data = base64.b64decode(encoded)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{user_id}_{timestamp}.png"
    filepath = os.path.join(RECEIPTS_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(image_data)

    # استدعاء OCR لاستخراج المبلغ
    amount = extract_amount_from_image_file(filepath)

    # حفظ السجل مع المبلغ المكتشف
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO transactions (user_id, image_path, amount, created_at) VALUES (?, ?, ?, ?)",
                  (user_id, filepath, amount, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()

    return RedirectResponse(url="/view", status_code=303)

@app.get("/view")
def view_transactions(request: Request):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/login", status_code=303)
    try:
        user_id = int(user_id_str)
    except ValueError:
        return RedirectResponse(url="/login", status_code=303)

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT id, image_path, amount, created_at FROM transactions WHERE user_id=? ORDER BY id DESC", (user_id,))
        trs = c.fetchall()
        c.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE user_id=?", (user_id,))
        total_row = c.fetchone()
        total_amount = float(total_row[0]) if total_row and total_row[0] is not None else 0.0

    # نمرر فقط المسارات والمبلغ الإجمالي لعرض بسيط
    return templates.TemplateResponse("view.html", {"request": request, "transactions": trs, "total_amount": total_amount})

@app.post("/delete/{id}")
def delete_transaction(id: int, request: Request):
    user_id_str = request.cookies.get("current_user")
    if not user_id_str:
        return RedirectResponse(url="/login", status_code=303)
    try:
        user_id = int(user_id_str)
    except ValueError:
        return RedirectResponse(url="/login", status_code=303)

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT image_path FROM transactions WHERE id=? AND user_id=?", (id, user_id))
        row = c.fetchone()
        if row and row[0] and os.path.exists(row[0]):
            try:
                os.remove(row[0])
            except Exception as e:
                print("Failed to remove file:", e)
        c.execute("DELETE FROM transactions WHERE id=? AND user_id=?", (id, user_id))
        conn.commit()

    return RedirectResponse(url="/view", status_code=303)

@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie("current_user")
    return resp
