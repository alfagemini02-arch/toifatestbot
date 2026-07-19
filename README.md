# Telegram Test Bot + Mini App

GitHub va Render orqali joylashtirishga tayyor, Telegram ichida ishlaydigan test tizimi.

Loyiha quyidagi qismlardan iborat:

- Telegram bot: ro‘yxatdan o‘tish, Mini App tugmasi, statistika, xatolik xabarlari, admin bilan aloqa va broadcast;
- foydalanuvchi Mini App: testlar ro‘yxati, random savollar, taymer, natija va javoblarni ko‘rish;
- admin panel: manbalar, savollar, global qidiruv, test builder, statistika va `.txt` / `.docx` / `.db` import;
- FastAPI backend va SQLAlchemy ma’lumotlar bazasi;
- bitta Docker servis orqali Render deploy;
- Telegram `initData` HMAC tekshiruvi, JWT va alohida admin avtorizatsiyasi.

## 1. Tayyor manzillar

Deploy qilingandan keyin:

| Bo‘lim | Manzil |
|---|---|
| Foydalanuvchi Mini App | `https://app.sizningdomeningiz.uz/app/` |
| Admin panel | `https://app.sizningdomeningiz.uz/admin/` |
| Health check | `https://app.sizningdomeningiz.uz/api/health` |
| API hujjatlari | faqat `DEBUG=true` bo‘lsa `/api/docs` |
| Telegram webhook | `/api/telegram/webhook` |

## 2. Papkalar

```text
telegram-test-miniapp/
├── backend/app/            # FastAPI, SQLAlchemy, Telegram bot
├── frontend-user/          # Telegram Mini App (Vite + TypeScript)
├── frontend-admin/         # Admin panel (Vite + TypeScript)
├── sample-data/            # Import uchun namuna
├── tests/                  # Avtomatik testlar
├── .github/workflows/      # GitHub Actions
├── Dockerfile
├── render.yaml
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## 3. Eng muhim ishlab chiqarish talabi: doimiy PostgreSQL

Render Web Service lokal diski doimiy ma’lumotlar bazasi sifatida ishlatilmasligi kerak. `SQLite` faqat lokal sinov uchun qoldirilgan.

Ishlab chiqarishda tashqi PostgreSQL connection string kiriting:

```env
DATABASE_URL=postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require
```

Neon, Supabase yoki boshqa PostgreSQL xizmati ishlatilishi mumkin. Tanlangan xizmatning amaldagi bepul tarif va limitlarini o‘z saytida tekshiring.

## 4. Telegram botni yaratish

1. Telegram’da `@BotFather` ni oching.
2. `/newbot` buyrug‘i bilan bot yarating.
3. Olingan tokenni saqlang — u `BOT_TOKEN` bo‘ladi.
4. Bot username’ini `@` belgisiz `BOT_USERNAME` ga yozing.
5. O‘z Telegram raqamli ID’ingizni `ADMIN_IDS` ga yozing. ID’ni ishonchli Telegram ID bot orqali aniqlashingiz mumkin.
6. BotFather ichida botning Menu Button manzilini quyidagicha sozlang:

```text
https://app.sizningdomeningiz.uz/app/
```

Botning o‘zidagi `📝 Testlarni boshlash` tugmasi ham shu manzilni avtomatik ochadi.

## 5. GitHub’ga joylash

Yangi bo‘sh repository yarating. ZIP’ni ochgach, loyiha papkasida:

```bash
git init
git add .
git commit -m "Initial Telegram Mini App"
git branch -M main
git remote add origin https://github.com/USERNAME/REPOSITORY.git
git push -u origin main
```

`.env` fayli GitHub’ga yuborilmaydi. Token, parol va maxfiy kalitlarni faqat Render Environment bo‘limiga kiriting.

## 6. Render’da deploy

### Blueprint orqali

1. Render hisobiga kiring.
2. **New + → Blueprint** ni tanlang.
3. GitHub repository’ni ulang.
4. Render `render.yaml` faylini topadi va bitta Docker Web Service yaratadi.
5. Quyidagi environment variables’ni kiriting.

### Majburiy Environment Variables

| Kalit | Misol / izoh |
|---|---|
| `BOT_TOKEN` | BotFather bergan token |
| `BOT_USERNAME` | `my_test_bot`, `@` belgisiz |
| `ADMIN_IDS` | `123456789` yoki vergul bilan bir nechta ID |
| `ADMIN_USERNAME` | Telegram admin username, `@` belgisiz |
| `WEBHOOK_SECRET` | uzun tasodifiy maxfiy matn |
| `WEBAPP_URL` | `https://app.sizningdomeningiz.uz` |
| `SECRET_KEY` | kamida 64 belgili tasodifiy kalit |
| `DATABASE_URL` | doimiy PostgreSQL connection string |
| `BOOTSTRAP_ADMIN_USERNAME` | admin panel login’i |
| `BOOTSTRAP_ADMIN_PASSWORD` | kuchli boshlang‘ich parol |
| `ALLOWED_ORIGINS` | `https://app.sizningdomeningiz.uz` |
| `TRUSTED_HOSTS` | `app.sizningdomeningiz.uz,*.onrender.com` |

Tasodifiy kalit yaratish:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

`WEBHOOK_SECRET` va `SECRET_KEY` uchun alohida qiymatlar yarating.

> `BOOTSTRAP_ADMIN_PASSWORD` faqat admin birinchi marta yaratilganda ishlatiladi. Birinchi deploydan oldin kuchli parol kiriting.

## 7. Dastlab Render URL bilan tekshirish

Custom domain ulanmasidan oldin Render bergan URL bilan tekshirish mumkin:

```env
WEBAPP_URL=https://telegram-test-miniapp-xxxx.onrender.com
ALLOWED_ORIGINS=https://telegram-test-miniapp-xxxx.onrender.com
TRUSTED_HOSTS=telegram-test-miniapp-xxxx.onrender.com,*.onrender.com
```

Deploy muvaffaqiyatli bo‘lgach:

```text
https://telegram-test-miniapp-xxxx.onrender.com/api/health
```

manzilida `{"status":"ok",...}` chiqishi kerak.

Keyin BotFather’dagi Menu Button URL’ni vaqtincha Render URL’ga qo‘yib tekshirish mumkin.

## 8. Domenni Render’ga ulash

Subdomen ishlatish tavsiya qilinadi:

```text
app.sizningdomeningiz.uz
```

1. Render servisida **Settings → Custom Domains → Add Custom Domain**.
2. `app.sizningdomeningiz.uz` ni kiriting.
3. Render ko‘rsatgan DNS qiymatini domen provayderingizda `CNAME` sifatida qo‘shing.
4. Render domain tasdiqlangach HTTPS sertifikatini ulaydi.
5. Environment variables’ni custom domain bilan yangilang:

```env
WEBAPP_URL=https://app.sizningdomeningiz.uz
ALLOWED_ORIGINS=https://app.sizningdomeningiz.uz
TRUSTED_HOSTS=app.sizningdomeningiz.uz,*.onrender.com
```

6. Render’da **Manual Deploy → Deploy latest commit**.
7. BotFather Menu Button URL’ni custom domain’ga o‘zgartiring.

Apex/root domen (`sizningdomeningiz.uz`) ishlatilsa, DNS provayder Render ko‘rsatgan `A`, `ANAME` yoki `ALIAS` yozuvini qo‘llashi kerak. Render’dagi ko‘rsatma asosiy manba hisoblanadi.

## 9. Birinchi kirish

1. Telegram botga `/start` yuboring.
2. Ism-familiyani kiriting.
3. O‘zingizga tegishli kontaktni yuboring.
4. `📝 Testlarni boshlash` tugmasini bosing.
5. Admin panelni oching:

```text
https://app.sizningdomeningiz.uz/admin/
```

6. `BOOTSTRAP_ADMIN_USERNAME` va `BOOTSTRAP_ADMIN_PASSWORD` bilan kiring.
7. Manba yarating yoki `sample-data/questions.txt` ni import qiling.
8. Test yarating va manbadan nechta savol tushishini belgilang.

## 10. Import formatlari

### TXT

```text
1. Savol matni?
A) Noto‘g‘ri variant
*B) To‘g‘ri variant
C) Noto‘g‘ri variant

2. Keyingi savol?
+To‘g‘ri javob
Noto‘g‘ri javob
Noto‘g‘ri javob
```

To‘g‘ri javob boshida `*`, `+` yoki `#` belgisi qo‘yiladi.

### DOCX

Quyidagilar qo‘llanadi:

- har bir savol alohida jadval;
- bitta katta jadval ichida raqamlangan savollar;
- jadvalsiz, TXT formatiga o‘xshash paragraflar.

### Eski SQLite `.db`

Kutiladigan asosiy jadvallar:

```text
sources
source_questions
source_answers
```

`source_answers.position` ustuni bo‘lmagan eski bazalar ham qo‘llanadi. `.db` fayl read-only rejimda tahlil qilinadi.

## 11. Lokal ishga tushirish

### Variant A — Docker Compose

`.env.example` dan `.env` yarating:

```bash
cp .env.example .env
```

Lokal qiymatlarni sozlang, so‘ng:

```bash
docker compose up --build
```

Manzillar:

```text
http://localhost:10000/app/
http://localhost:10000/admin/
http://localhost:10000/api/health
```

Telegram Mini App real Telegram ichida faqat HTTPS orqali ochiladi. Lokal frontend/API sinovi uchun `ENABLE_DEV_AUTH=true` ishlatilishi mumkin; ishlab chiqarishda bu qiymat mutlaqo `false` bo‘lishi shart.

### Variant B — Python + Node

Backend:

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements-dev.txt
python -m backend.app.cli init-db
python -m backend.app.cli seed-demo
python -m uvicorn backend.app.main:app --reload --port 10000
```

Frontend user:

```bash
cd frontend-user
npm install
npm run dev
```

Frontend admin:

```bash
cd frontend-admin
npm install
npm run dev
```

Botni lokal polling rejimida alohida ishga tushirish:

```bash
python -m backend.app.polling
```

## 12. Test va tekshiruv

```bash
ruff check backend tests
pytest -q

cd frontend-user
npm ci
npm run build

cd ../frontend-admin
npm ci
npm run build
```

GitHub Actions har push’da shu tekshiruvlarni bajaradi.

## 13. Xavfsizlik

- Telegram foydalanuvchisi `initData` HMAC imzosi bilan tasdiqlanadi;
- `initDataUnsafe` ga server darajasida ishonilmaydi;
- to‘g‘ri javob savol yuklanganda frontendga berilmaydi;
- foydalanuvchi faqat o‘z test urinishiga kira oladi;
- admin va user JWT tokenlari alohida rol bilan yaratiladi;
- admin login’i 3 ta xato urinishdan so‘ng vaqtincha cheklanadi;
- parollar PBKDF2-SHA256 bilan hash qilinadi;
- SQLAlchemy parametrli so‘rovlar ishlatadi;
- `.db` import read-only rejimda ochiladi;
- fayl turi va 20 MB hajm cheklovi tekshiriladi;
- `BOT_TOKEN`, `SECRET_KEY`, DB paroli va admin paroli repository’ga yozilmaydi.

## 14. Render bepul rejimida kutiladigan holat

Bepul Web Service ma’lum vaqt trafik bo‘lmasa uyqu rejimiga o‘tishi mumkin. Birinchi ochilishda server uyg‘onishi sababli kechikish bo‘ladi. Ma’lumotlar alohida doimiy PostgreSQL’da turgani uchun servis qayta ishga tushsa ham savollar va natijalar saqlanadi.

## 15. Muhim fayllar

- `.env.example` — barcha sozlamalar namunasi;
- `render.yaml` — Render Blueprint;
- `Dockerfile` — frontend build + backend runtime;
- `sample-data/questions.txt` — import namunasi;
- `tests/` — xavfsizlik, import, test dvigateli va health endpoint testlari.

## 16. Muammolarni aniqlash

Render logida tekshiriladigan asosiy holatlar:

- `BOT_TOKEN sozlanmagan` — token kiritilmagan;
- `Telegram webhook o'rnatildi` — webhook muvaffaqiyatli;
- `database connection` xatosi — `DATABASE_URL` noto‘g‘ri yoki DB SSL talabi bajarilmagan;
- `Invalid host header` — `TRUSTED_HOSTS` ga domen qo‘shilmagan;
- CORS xatosi — `ALLOWED_ORIGINS` da aynan `https://...` manzil kiritilmagan;
- Mini App “avval /start” desa — foydalanuvchi botda ro‘yxatdan o‘tmagan.

## Litsenziya

MIT License. Maxfiy kalitlar va real foydalanuvchi ma’lumotlarini ochiq repository’ga joylamang.
