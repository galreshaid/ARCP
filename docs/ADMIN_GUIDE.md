# AAML RadCore Platform - دليل مدير النظام (Admin Guide)

## 📋 جدول المحتويات
1. [إدارة المستشفيات والأجهزة](#facilities-modalities)
2. [إدارة البروتوكولات](#protocols)
3. [إدارة الفحوصات](#exams)
4. [الربط بين البروتوكولات والفحوصات](#protocol-assignment)
5. [استيراد وتصدير البيانات](#import-export)

---

## 1️⃣ إدارة المستشفيات والأجهزة {#facilities-modalities}

### إضافة مستشفى جديد (Facility)

1. اذهب إلى **Admin Panel** → **Core** → **Facilities**
2. اضغط **Add Facility**
3. املأ البيانات:
   - **Code**: رمز فريد للمستشفى (مثل: `HOSP01`)
   - **Name**: اسم المستشفى
   - **HL7 Facility ID**: المعرف في نظام HL7
   - **Is Active**: فعّل هذا الخيار
4. احفظ

### إضافة جهاز جديد (Modality)

1. اذهب إلى **Core** → **Modalities**
2. اضغط **Add Modality**
3. املأ البيانات:
   - **Code**: رمز الجهاز (CT, MR, XR, US, etc.)
   - **Name**: اسم الجهاز بالكامل
   - **Requires QC**: هل يحتاج لفحص جودة؟
   - **Is Active**: فعّل
4. احفظ

---

## 2️⃣ إدارة البروتوكولات {#protocols}

### إضافة بروتوكول يدويًا

1. اذهب إلى **Protocols** → **Protocol Templates**
2. اضغط **Add Protocol Template**
3. املأ البيانات الأساسية:

#### التعريف (Identification)
- **Code**: رمز فريد (مثل: `CT_HEAD_STROKE`)
- **Name**: اسم البروتوكول (مثل: "CT Head for Acute Stroke")
- **Version**: رقم الإصدار (مثل: 1.0)

#### التصنيف (Classification)
- **Modality**: اختر الجهاز (CT, MR, etc.)
- **Facility**: اختياري - حدد مستشفى معين أو اتركه فارغًا لجميع المستشفيات
- **Body Part**: جزء الجسم (Head, Chest, Abdomen, etc.)
- **Laterality**: الجانب (Left, Right, Bilateral, Not Applicable)

#### المحتوى (Content)
- **Description**: وصف البروتوكول
- **Technical Parameters**: JSON - معلمات التصوير
  ```json
  {
    "kVp": 120,
    "mAs": 250,
    "slice_thickness": "5mm",
    "reconstruction": "standard"
  }
  ```
- **Instructions**: تعليمات التصوير (نقطة بنقطة)
- **Clinical Keywords**: كلمات مفتاحية للبحث
  ```json
  ["stroke", "acute", "neurological deficit"]
  ```

#### إعدادات Contrast
- **Requires Contrast**: هل يحتاج صبغة؟
- **Contrast Phase**: نوع المرحلة (Arterial, Venous, etc.)

#### الأولوية
- **Priority**: رقم أقل = أولوية أعلى (1-999)
- **Is Active**: فعال؟
- **Is Default**: افتراضي لهذا الجهاز/الجزء؟

4. احفظ

### العمليات الجماعية (Bulk Actions)

#### تفعيل بروتوكولات متعددة
1. حدد البروتوكولات من القائمة
2. اختر **Actions** → **Activate selected protocols**
3. اضغط **Go**

#### تعطيل بروتوكولات
1. حدد البروتوكولات
2. **Actions** → **Deactivate selected protocols**

#### تعيين كـ Default
1. حدد بروتوكول واحد
2. **Actions** → **Set as default protocol**

#### نسخ بروتوكول
1. حدد البروتوكولات للنسخ
2. **Actions** → **Duplicate selected protocols**
3. سيتم إنشاء نسخ بـ `_COPY` في نهاية الكود

#### تصدير إلى CSV
1. حدد البروتوكولات
2. **Actions** → **Export protocols to CSV**
3. سيتم تنزيل ملف CSV

---

## 3️⃣ إدارة الفحوصات {#exams}

### إضافة فحص يدويًا

1. اذهب إلى **Core** → **Exams**
2. اضغط **Add Exam**
3. املأ البيانات:

#### المعرفات
- **Accession Number**: رقم فريد للفحص
- **Order ID**: رقم الطلب
- **MRN**: الرقم الطبي للمريض

#### تفاصيل الفحص
- **Facility**: المستشفى
- **Modality**: نوع الجهاز
- **Procedure Code**: كود الإجراء (اختياري)
- **Procedure Name**: اسم الإجراء
- **Scheduled DateTime**: موعد الفحص
- **Exam DateTime**: وقت الفحص الفعلي
- **Status**: الحالة (SCHEDULED, COMPLETED, etc.)

#### معلومات المريض
- **Patient Name**: الاسم
- **Patient DOB**: تاريخ الميلاد
- **Patient Gender**: الجنس (M/F)

#### السياق الطبي
- **Clinical History**: التاريخ المرضي
- **Reason for Exam**: سبب الفحص

4. احفظ

### عرض تفاصيل الفحص

في صفحة الفحص، يمكنك رؤية:
- ✅ **Has Protocol**: هل له بروتوكول؟
- ✅ **QC Status**: حالة فحص الجودة (PASS/FAIL/CONDITIONAL)
- ✅ **Contrast Status**: هل تم توثيق الصبغة؟

### روابط سريعة
- **Protocol Link**: رابط للبروتوكول المعين
- **QC Link**: رابط لفحص الجودة
- **Contrast Link**: رابط لتوثيق الصبغة

---

## 4️⃣ الربط بين البروتوكولات والفحوصات {#protocol-assignment}

### الطريقة 1: التعيين الفردي

1. اذهب إلى **Protocols** → **Protocol Assignments**
2. اضغط **Add Protocol Assignment**
3. اختر:
   - **Exam**: الفحص المطلوب
   - **Protocol**: البروتوكول المناسب
4. احفظ

### الطريقة 2: التعيين الجماعي (Bulk Assignment)

هذه الطريقة الأسهل! ✨

1. اذهب إلى **Protocols** → **Protocol Templates**
2. ابحث عن البروتوكول المطلوب وحدده
3. من **Actions** اختر **Bulk assign to exams**
4. اضغط **Go**
5. ستفتح صفحة جديدة تعرض:
   - تفاصيل البروتوكول المحدد
   - قائمة بالفحوصات المرشحة (نفس الجهاز، بدون بروتوكول)
6. حدد الفحوصات المطلوبة:
   - استخدم **Select All** لتحديد الكل
   - أو حدد يدويًا
7. اضغط **Assign Protocol to Selected Exams**

### الطريقة 3: من صفحة الفحص

1. افتح الفحص المطلوب
2. في صفحة التعديل، ابحث عن **Protocol Assignment** في الـ Inlines
3. أضف تعيين جديد

---

## 5️⃣ استيراد وتصدير البيانات {#import-export}

### استيراد بروتوكولات من CSV

#### تحضير ملف CSV

أنشئ ملف CSV بهذه الأعمدة:
```csv
code,name,modality_code,facility_code,body_part,laterality,requires_contrast,contrast_phase,description,instructions,clinical_keywords,priority,is_active,is_default
```

مثال (انظر `data/sample_protocols.csv`):
```csv
CT_HEAD_STROKE,CT Head for Acute Stroke,CT,,Head,NOT_APPLICABLE,false,NON_CONTRAST,Non-contrast CT head for acute stroke evaluation,1. Scout image\n2. Axial slices 5mm,stroke;acute,10,true,true
```

#### تشغيل الاستيراد

```bash
# استيراد عادي (إنشاء فقط)
python manage.py import_protocols /path/to/protocols.csv

# استيراد مع التحديث (إنشاء أو تحديث)
python manage.py import_protocols /path/to/protocols.csv --update

# تجربة بدون حفظ (Dry Run)
python manage.py import_protocols /path/to/protocols.csv --dry-run
```

### تصدير بروتوكولات إلى CSV

```bash
# تصدير الكل
python manage.py export_protocols protocols_export.csv

# تصدير حسب جهاز معين
python manage.py export_protocols protocols_export.csv --modality CT

# تصدير حسب مستشفى
python manage.py export_protocols protocols_export.csv --facility HOSP01

# تصدير البروتوكولات الفعالة فقط
python manage.py export_protocols protocols_export.csv --active-only
```

---

## 🎯 نصائح وأفضل الممارسات

### 1. تسمية البروتوكولات
- استخدم أكواد واضحة: `MODALITY_BODYPART_INDICATION`
- مثال: `CT_HEAD_STROKE`, `MR_KNEE_LEFT_TRAUMA`

### 2. الأولويات (Priority)
- 1-10: بروتوكولات طارئة (Emergency)
- 11-50: بروتوكولات شائعة
- 51-100: بروتوكولات خاصة
- 100+: بروتوكولات نادرة

### 3. الكلمات المفتاحية (Clinical Keywords)
- أضف كلمات شائعة في التاريخ المرضي
- مثال: `["stroke", "acute", "neurological deficit", "CVA"]`
- النظام سيستخدمها للاقتراحات التلقائية

### 4. تنظيم حسب المستشفى
- اترك **Facility** فارغًا للبروتوكولات العامة
- حدد مستشفى معين للبروتوكولات الخاصة

### 5. الصيانة الدورية
- راجع البروتوكولات غير المستخدمة شهريًا
- حدّث الإصدارات (Versions) عند التعديل
- استخدم **Supersedes** لربط الإصدارات القديمة بالجديدة

---

## ❓ الأسئلة الشائعة

**س: كيف أحذف بروتوكول؟**
ج: لا تحذف! استخدم **Deactivate** لإخفائه من الاستخدام مع الاحتفاظ بالسجلات

**س: ماذا لو أردت تعديل بروتوكول مستخدم؟**
ج: انسخه (Duplicate)، عدّل النسخة، فعّلها، وعطّل القديم

**س: كيف أعرف أي بروتوكول الأكثر استخدامًا؟**
ج: انظر عمود **Usage** في قائمة البروتوكولات (ملون حسب الاستخدام)

**س: البروتوكولات لا تظهر في الاقتراحات؟**
ج: تأكد من:
- البروتوكول **Is Active**
- **Modality** يطابق الفحص
- **Clinical Keywords** موجودة وصحيحة

---

## 🆘 الدعم

للمساعدة، راجع:
- Documentation: `/docs/`
- API Docs: `/api/schema/swagger/`
- Admin Help: داخل كل صفحة admin

---

**تم التحديث**: {{ now }}