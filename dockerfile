# استخدم صورة Python الرسمية
FROM python:3.13-slim

# إعداد متغير بيئة لتثبيت الحزم بدون ملفات pycache
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# إنشاء مجلد التطبيق
WORKDIR /app

# نسخ ملفات requirements أولًا لتقليل إعادة البناء
COPY requirements.txt .

# تثبيت المتطلبات
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي ملفات المشروع
COPY . .

# فتح البورت الافتراضي لـ FastAPI
EXPOSE 8000

# الأمر الافتراضي لتشغيل FastAPI مع Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
