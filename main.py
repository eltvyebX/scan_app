import os
import sqlite3
from datetime import datetime

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates

from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from reportlab.lib import colors

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

templates = Jinja2Templates(directory="templates")

# --------------------------------------------------
# Routes
# --------------------------------------------------

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/confirm")
def confirm_data(
    request: Request,
    trx_last4: str = Form(...),
    amount: float = Form(...),
    date_time: str = Form(...)
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


# --------------------------------------------------
# Export PDF
# --------------------------------------------------

@app.get("/export-pdf")
def export_pdf():
    pdf_path = "transactions_report.pdf"

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions ORDER BY id DESC")
        trs = cursor.fetchall()

    doc = SimpleDocTemplate(pdf_path)
    data = [["ID", "Last 4", "Date", "Amount"]]

    for t in trs:
        data.append([t["id"], t["trx_last4"], t["trx_date"], str(t["amount"])])

    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        ('ALIGN', (0,0), (-1,-1), 'CENTER')
    ]))

    doc.build([table])

    return FileResponse(pdf_path, media_type="application/pdf", filename="transactions.pdf")
