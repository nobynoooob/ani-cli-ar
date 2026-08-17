<div align="center" dir="rtl">

<h2>💖 ادعم هذا المشروع المفتوح المصدر</h2>
<p>دعمك يساعدنا في صيانة الخوادم واستمرار تطوير التحديثات!</p>
<a href="https://paypal.me/np4abdou">
  <img src="https://img.shields.io/badge/Donate_with_PayPal-00457C?style=for-the-badge&logo=paypal&logoColor=white" alt="Donate with PayPal">
</a>
<br><br><br>

**سطر أوامر خفيف لبث الأنمي مع ترجمات عربية**

<p align="center">
  <a href="https://github.com/nobynoooob/ani-cli-ar/stargazers">
    <img src="https://img.shields.io/github/stars/nobynoooob/ani-cli-ar?style=for-the-badge" />
  </a>
  <a href="https://github.com/nobynoooob/ani-cli-ar/network">
    <img src="https://img.shields.io/github/forks/nobynoooob/ani-cli-ar?style=for-the-badge" />
  </a>
  <br>
  <a href="https://github.com/nobynoooob/ani-cli-ar/releases">
    <img src="https://img.shields.io/github/v/release/nobynoooob/ani-cli-ar?style=for-the-badge" />
  </a>
  <a href="https://pypi.org/project/ani-cli-arabic">
    <img src="https://img.shields.io/pypi/v/ani-cli-arabic?style=for-the-badge" />
  </a>
  <a href="https://aur.archlinux.org/packages/ani-cli-arabic">
    <img src="https://img.shields.io/aur/version/ani-cli-arabic?style=for-the-badge" />
  </a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/License-GPL--3.0-green?style=for-the-badge" />
</p>

<p>لاختيار اللغة الإنجليزية اضغط على الزر:</p>
<a href="README.md">
  <img src="https://img.shields.io/badge/Language-English-blue?style=for-the-badge&logo=google-translate&logoColor=white" alt="English">
</a>

<br>
<br>

</div>

---

<div dir="rtl">

## 📑 التنقل

[التثبيت](#-التثبيت) • [الميزات](#-ماذا-يمكنك-أن-تفعل) • [كيفية الاستخدام](#-كيفية-الاستخدام) • [اختصارات لوحة المفاتيح](#️-اختصارات-لوحة-المفاتيح) • [الإعدادات](#️-الإعدادات) • [البناء والنشر](#-البناء-والنشر) • [المساهمون](#-المساهمون) • [الرخصة](#-الرخصة)

---

## 📦 التثبيت

### المتطلبات
قبل التثبيت، تأكد من توفر:
- **بايثون 3.8 أو أحدث** (يُنصح ببايثون 3.12)
- **مشغل الوسائط MPV أو VLC** (للبث)
- **Playwright Chromium** — يُثبَّت تلقائياً عند أول بث (لا حاجة لأي خطوة يدوية)

### الطريقة الأولى: مُثبّت سطر واحد (مُستحسن)

</div>

```bash
curl -fsSL https://raw.githubusercontent.com/nobynoooob/ani-cli-ar/main/install.sh | sh
```

<div dir="rtl">

يكتشف هذا تلقائياً بيئتك (لينكس، ماك، تيرمكس) ويُثبّت عبر `pipx` أو `pip`.

### الطريقة الثانية: التثبيت عبر pip / pipx

</div>

```bash
# مباشرة من GitHub (الأحدث دائماً)
pip install git+https://github.com/nobynoooob/ani-cli-ar.git

# أو من PyPI (إصدارات مستقرة)
pip install ani-cli-ar

# أو عبر pipx (بيئة معزولة، مُستحسن)
pipx install ani-cli-ar
```

<div dir="rtl">

تشغيل التطبيق:

</div>

```bash
ani-cli-ar
```

<div dir="rtl">

للتحديث إلى أحدث إصدار:

</div>

```bash
pip install --upgrade ani-cli-ar
```

<div dir="rtl">

### الطريقة الثالثة: آرتش لينكس (AUR)

</div>

```bash
yay -S ani-cli-arabic
# أو
paru -S ani-cli-arabic
```

<div dir="rtl">

### الطريقة الرابعة: ملفات تنفيذية جاهزة (لينكس / ويندوز)

حمّل الملفات التنفيذية المستقلة من [صفحة الإصدارات](https://github.com/nobynoooob/ani-cli-ar/releases):
- `ani-cli-ar-cli-linux.tar.gz` — فك الضغط ثم شغّل `./ani-cli-ar-cli` (أو `./install.sh`)
- `ani-cli-ar-cli-windows.zip` — فك الضغط وشغّل `ani-cli-ar-cli-windows.exe`

لا تحتاج بايثون مع الملفات التنفيذية الجاهزة؛ كل ما يلزم هو **MPV** (أو VLC).

### الطريقة الخامسة: من المصدر (للتطوير)

</div>

```bash
git clone https://github.com/nobynoooob/ani-cli-ar.git
cd ani-cli-ar
pip install -e .
ani-cli-ar
```

---

<div dir="rtl">

## 🎯 ماذا يمكنك أن تفعل؟

إليك كل ما تقدمه هذه الأداة:

### البث والتشغيل
- **خيارات جودة متعددة**: شاهد بدقة 1080p أو 720p أو 480p حسب سرعة الإنترنت لديك
- **التنزيل الجماعي**: نزّل عدة حلقات دفعة واحدة للمشاهدة بلا اتصال
- **دعم الإعلانات التشويقية**: شاهد إعلانات يوتيوب التشويقية قبل البدء بالأنمي
- **الاستئناف من السجل**: تابع من حيث توقفت بالضبط
- **دعم MPV/VLC**: اختر مشغل الوسائط المفضل لديك (تُطبَّق خيارات التخزين المؤقت للاتصالات البطيئة)

### الاكتشاف والتصفح
- **بحث عن الأنمي**: ابحث عن أي أنمي أو فيلم أنمي بالاسم (يدعم العناوين الإنجليزية واليابانية والعربية)
- **الرائج الآن**: شاهد ما هو شائع حالياً
- **الأعلى تقييماً**: تصفح أعلى الأنمي تقييماً على الإطلاق
- **التصفح حسب الفئة**: صنّف حسب الأكشن، الرومانسية، الإيسيكاي، و12 فئة أخرى
- **التصفح حسب الاستوديو**: ابحث عن أنمي من استوديوهات Toei Animation وMAPPA وUfotable وأكثر من 20 استوديو آخر
- **أحدث الإصدارات**: ابقَ على اطلاع بأحدث الأنمي

### مساران: إنجليزي + عربي
- **الإنجليزي**: سلسلة موفّرين متعددة — Miruro وHiAnime وAllAnime وAPI وMkissa وGogoAnime (سلسلة مزوّدين مع عزل فشل لكل خطوة)
- **العربي**: مسار عربي مخصص عبر واجهة برمجة الأنمي (`AnimeAPI`) مع اختيار الجودة ومسارات الترجمة العربية

### المكتبة الشخصية
- **سجل المشاهدة**: تتبع كل ما شاهدته مع الطوابع الزمنية
- **نظام المفضلة**: ضع إشارة مرجعية على أنميك المفضل للوصول السريع
- **تتبع الحلقات**: التطبيق يتذكر في أي حلقة أنت

### الواجهة والتجربة
- **واجهة طرفية غنية (TUI)**: واجهة طرفية جميلة مبنية بمكتبة Rich
- **17 سمة لونية**: أزرق، أحمر، أخضر، بنفسجي، سماوي، أصفر، وردي، برتقالي، أزرق مخضر، أرجواني، ليموني، مرجاني، خزامى، ذهبي، نعناعي، زهري، الغروب
- **حضور Discord الغني**: أظهر ما تشاهده على Discord مع ملصقات الأنمي
- **تنقل سلس**: أزرار تحكم بديهية
- **وضع سطر أوامر مبسّط**: `--interactive "ناروتو"` للبحث السريع (يُستخدم تلقائياً أيضاً إذا كانت الطرفية ضيقة)

### المميزات التقنية
- **بلا إعلانات**: تجربة بث نظيفة
- **تحديثات تلقائية**: فاحص إصدارات مدمج يخطرك بالإصدارات الجديدة (يمكن إيقافه)
- **مُثبّت تلقائي للمتطلبات**: يفحص ويُثبّت المتطلبات المفقودة تلقائياً
- **متعدد المنصات**: يعمل على لينكس وويندوز وماك (يدعم تيرمكس)

---

## 🎮 كيفية الاستخدام

1. **شغّل التطبيق**: نفّذ `ani-cli-arabic` أو `ani-cli-ar`
2. **تصفح أو ابحث**: استخدم القائمة الرئيسية للبحث، أو عرض الرائج، أو تصفح الفئات
3. **اختر أنمي**: تنقّل بأزرار الأسهم واضغط Enter
4. **اختر حلقة**: اختر أي حلقة تريد مشاهدتها
5. **اختر الجودة**: اختر جودة الفيديو المفضلة لديك
6. **استمتع**: سيُشغَّل MPV (أو VLC) ويبدأ البث

يمكنك أيضاً استخدام الوضع التفاعلي للبحث السريع:

</div>

```bash
ani-cli-ar -i "ون بيس"
```

---

<div dir="rtl">

## ⌨️ اختصارات لوحة المفاتيح

| المفتاح | الوظيفة |
|---------|----------|
| **↑ / ↓** | التنقل عبر القوائم |
| **Enter** | اختيار/تأكيد الخيار |
| **G** | الانتقال مباشرة إلى رقم حلقة |
| **B** | العودة إلى الشاشة السابقة |
| **Q / Esc** | الخروج من التطبيق |
| **Space** | إيقاف/استئناف الفيديو (في المشغل) |
| **← / →** | الترجيع/التقديم 5 ثوان |
| **F** | تبديل ملء الشاشة |

---

## ⚙️ الإعدادات

يتم حفظ الإعدادات محلياً في `~/.ani-cli-arabic/database/config.json`

ادخل قائمة الإعدادات من الشاشة الرئيسية للتخصيص:

- **الجودة الافتراضية**: 1080p أو 720p أو 480p
- **مشغل الوسائط**: MPV أو VLC
- **الحلقة التالية تلقائياً**: تبديل الانتقال التلقائي للحلقة
- **حضور Discord الغني**: إظهار أو إخفاء نشاط Discord
- **سمة اللون**: اختر من بين 17 مخطط لوني
- **التحليلات**: الاشتراك/إلغاء الاشتراك في إحصائيات الاستخدام المجهولة (مفعّل افتراضياً)
- **فحص التحديثات**: تبديل إشعارات التحديث التلقائية

يمكنك أيضاً تعديل ملف الإعدادات يدوياً إذا أردت.

---

## 🔧 البناء والنشر (للمشرفين)

ابنِ الملف التنفيذي للسطر أوامر عبر `build_cli.py` (PyInstaller، مع استبعاد كل أطر العمل الرسومية):

</div>

```bash
python build_cli.py                          # dist/ani-cli-ar-cli
python build_cli.py --zip                    # مع إنتاج حزمة .zip محمولة أيضاً
python build_cli.py --exclude-module numpy   # استثناءات إضافية
```

<div dir="rtl">

تُبنى الإصدارات تلقائياً بواسطة `.github/workflows/build.yml` عند دفع وسوم `v*`
(`ani-cli-ar-cli-linux.tar.gz`، `ani-cli-ar-cli-windows.zip`). متصفح Playwright Chromium
**غير مضمّن** — يُنزَّل عند أول استخدام.

---

## 👥 المساهمون

</div>

<div align="center">

[![Contributors](https://contrib.rocks/image?repo=nobynoooob/ani-cli-ar)](https://github.com/nobynoooob/ani-cli-ar/graphs/contributors)

</div>

<div dir="rtl">

**المساهمون الرئيسيون:**
- [@np4abdou1](https://github.com/np4abdou1) - المنشئ والمطور الرئيسي
- [@Anas-Tou](https://github.com/Anas-Tou) - مساهم

تريد المساهمة؟ لا تتردد في فتح قضية أو تقديم طلب سحب!

---

## 📄 الرخصة

هذا المشروع مرخص بموجب **رخصة جنو العمومية الإصدار 3.0**.

يمكنك استخدام وتعديل وتوزيع هذا البرنامج بحرية تحت شروط رخصة GPL-3.0. راجع ملف [LICENSE](LICENSE) للنص القانوني الكامل.

**ببساطة:**
- ✅ استخدمه لأغراض شخصية أو تجارية
- ✅ عدّل الكود المصدري
- ✅ وزّعه على الآخرين
- ⚠️ أي تعديلات يجب أن تكون مفتوحة المصدر أيضاً تحت GPL-3.0
- ⚠️ قم بتضمين إشعار حقوق النشر الأصلي

</div>

---

<div align="center" dir="rtl">

### ⚠️ إشعار مهم

</div>

<div dir="rtl">

> [! CAUTION]
> **باستخدامك لهذا البرنامج أنت تفهم:**
>
> - يتم جمع إحصائيات استخدام مجهولة لشعار إحصائيات صفحة GitHub (يمكن تعطيلها في الإعدادات)
> - المشروع مرخص بموجب GPL-3.0 - راجع [LICENSE](LICENSE) للتفاصيل
> - نحن لا نستضيف أي محتوى؛ جميع البث من مصادر خارجية
> - هذه الأداة للاستخدام الشخصي والأغراض التعليمية فقط

---

<br>

صُنع بـ ❤️ من مجتمع الأنمي

[⭐ ضع نجمة لهذا المستودع](https://github.com/nobynoooob/ani-cli-ar) | [🐛 أبلغ عن الأخطاء](https://github.com/nobynoooob/ani-cli-ar/issues) | [💬 النقاشات](https://github.com/nobynoooob/ani-cli-ar/discussions)

</div>
