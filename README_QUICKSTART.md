# 🚀 AAML RadCore Platform - Quick Start

## تشغيل سريع في 5 دقائق

### 1️⃣ Setup البيئة

```bash
# Clone the repository (إذا لم يكن موجود)
git clone <your-repo>
cd aip-platform

# Run setup script
chmod +x setup_and_run.sh
./setup_and_run.sh
```

سيقوم السكريبت بـ:
- ✅ إنشاء virtual environment
- ✅ تثبيت المكتبات
- ✅ إنشاء database migrations
- ✅ تشغيل migrations
- ✅ إنشاء superuser (اختياري)

### 2️⃣ تحميل البيانات التجريبية

```bash
# Activate environment
source venv/bin/activate

# Load test data
python manage.py load_initial_data
```

سيتم إنشاء:
- 🏥 3 مستشفيات
- 🔬 5 أجهزة تصوير
- 👥 3 مستخدمين
- 📋 6 بروتوكولات
- 📊 20 فحص تجريبي

### 3️⃣ تشغيل السيرفر

```bash
python manage.py runserver
```

افتح: **http://localhost:8000/admin/**

---

## 👥 المستخدمين التجريبيين

| البريد الإلكتروني | كلمة المرور | الدور |
|-------------------|-------------|-------|
| `radiologist@test.com` | `password123` | Radiologist |
| `tech@test.com` | `password123` | Technologist |
| `supervisor@test.com` | `password123` | Supervisor |

---

## 🧪 اختبار النظام

### اختبار تلقائي
```bash
python test_protocol_system.py
```

### اختبار يدوي
اتبع: `TESTING_CHECKLIST.md`

---

## 📦 استيراد بروتوكولات إضافية

```bash
# من ملف CSV جاهز
python manage.py import_protocols data/sample_protocols.csv

# من ملفك الخاص
python manage.py import_protocols /path/to/your/protocols.csv --update
```

### مثال ملف CSV:

```csv
code,name,modality_code,body_part,requires_contrast,priority,is_active
CT_HEAD_STROKE,CT Head Acute Stroke,CT,Head,false,10,true
MR_BRAIN_WO,MRI Brain Without Contrast,MR,Head,false,10,true
```

---

## 🎯 المهام الشائعة

### إضافة بروتوكول جديد
1. Admin → **Protocols** → **Protocol Templates**
2. Click **Add Protocol Template**
3. املأ البيانات
4. Save

### ربط بروتوكول مع فحوصات (Bulk)
1. Admin → **Protocol Templates**
2. حدد بروتوكول واحد
3. Actions → **Bulk assign to exams**
4. حدد الفحوصات
5. Assign

### تصدير البروتوكولات
```bash
python manage.py export_protocols output.csv --active-only
```

---

## 🔧 إعدادات مهمة (Settings)

### Database (PostgreSQL)
تأكد من وجود PostgreSQL وعمله:
```bash
psql -U postgres
CREATE DATABASE aip_db;
```

### Redis (لـ Celery)
```bash
# Install Redis
brew install redis  # macOS
sudo apt install redis  # Ubuntu

# Start Redis
redis-server
```

### Environment Variables (.env)
راجع ملف `.env` وعدّل:
- `SECRET_KEY` للإنتاج
- `DB_PASSWORD` 
- `SITE_URL` للـ deep links

---

## 📊 API Endpoints

### Protocol Suggestions
```bash
GET /api/protocols/suggestions/?exam_id=<UUID>
```

### Protocol Templates
```bash
GET /api/protocols/templates/
GET /api/protocols/templates/<id>/
```

### Protocol Assignments
```bash
GET /api/protocols/assignments/
POST /api/protocols/assignments/
```

### API Docs
**Swagger UI**: http://localhost:8000/api/schema/swagger/

---

## 🐛 Troubleshooting

### مشكلة: Database connection refused
```bash
# تأكد من تشغيل PostgreSQL
sudo service postgresql start  # Linux
brew services start postgresql  # macOS
```

### مشكلة: ModuleNotFoundError
```bash
# تأكد من تفعيل virtual environment
source venv/bin/activate
pip install -r requirements.txt
```

### مشكلة: No such table
```bash
# تشغيل migrations
python manage.py migrate
```

### مشكلة: CSRF token missing
- تأكد من تسجيل الدخول أولًا
- استخدم session authentication للـ API testing

---

## 📚 الوثائق الكاملة

- **Admin Guide**: `docs/ADMIN_GUIDE.md`
- **Testing Checklist**: `TESTING_CHECKLIST.md`
- **API Documentation**: `http://localhost:8000/api/schema/swagger/`

---

## ✅ Checklist قبل البدء

- [ ] PostgreSQL مثبت وشغال
- [ ] Redis مثبت (optional - للـ Celery)
- [ ] Python 3.9+ متوفر
- [ ] Virtual environment مفعل
- [ ] Migrations تم تشغيلها
- [ ] Test data تم تحميله
- [ ] Superuser تم إنشاؤه
- [ ] Server شغال على port 8000

---

## 🚀 الخطوات التالية

بعد التأكد من عمل Protocol Module:

1. ✅ **Test thoroughly** - استخدم TESTING_CHECKLIST.md
2. 🎯 **Review Admin UI** - تأكد من سهولة الاستخدام
3. 📊 **Check Performance** - تأكد من السرعة
4. 🔄 **Move to Contrast Module** - بعد التأكد من كل شيء

---

**Need Help?** Check the troubleshooting section or review error logs in `logs/aip.log`