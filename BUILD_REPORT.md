# Build va test hisoboti

Versiya: 1.0.0  
Sana: 2026-07-19

Tekshirildi:

- Python `compileall`: muvaffaqiyatli;
- Ruff: xatosiz;
- Pytest: 7 ta test muvaffaqiyatli;
- User frontend TypeScript/Vite build: muvaffaqiyatli;
- Admin frontend TypeScript/Vite build: muvaffaqiyatli;
- FastAPI health endpoint: muvaffaqiyatli;
- Admin CRUD → test yaratish → user attempt → javob → finish integratsion oqimi: muvaffaqiyatli;
- Telegram `initData` HMAC tekshiruvi: testlangan;
- TXT va eski `position` ustunisiz SQLite import: testlangan.

Muhitda Docker dasturi mavjud bo‘lmagani sababli lokal `docker build` bu yerda bajarilmadi. Dockerfile multi-stage build bo‘lib, frontendlar `npm ci && npm run build`, runtime esa `requirements.txt` orqali yig‘iladi. GitHub Actions shu buildlarni har push’da tekshiradi.
