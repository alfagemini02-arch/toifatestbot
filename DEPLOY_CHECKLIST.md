# Deploy nazorat ro‘yxati

## Telegram
- [ ] BotFather orqali bot yaratildi
- [ ] `BOT_TOKEN` olindi
- [ ] `BOT_USERNAME` `@` belgisiz yozildi
- [ ] `ADMIN_IDS` to‘g‘ri Telegram ID bilan kiritildi
- [ ] BotFather Menu Button URL `/app/` bilan sozlandi

## Ma’lumotlar bazasi
- [ ] Doimiy PostgreSQL yaratildi
- [ ] `DATABASE_URL` SSL parametri bilan kiritildi
- [ ] DB paroli GitHub’ga yozilmadi

## Render
- [ ] GitHub repository ulandi
- [ ] `render.yaml` Blueprint topildi
- [ ] Barcha `sync: false` environment variables kiritildi
- [ ] `/api/health` 200 javob qaytardi
- [ ] Render logida webhook o‘rnatilgani ko‘rindi

## Domen
- [ ] Custom domain Render’ga qo‘shildi
- [ ] DNS yozuvi qo‘shildi
- [ ] HTTPS sertifikat faol
- [ ] `WEBAPP_URL`, `ALLOWED_ORIGINS`, `TRUSTED_HOSTS` yangilandi
- [ ] BotFather URL custom domain’ga o‘zgartirildi

## Admin panel
- [ ] Kuchli boshlang‘ich admin paroli belgilandi
- [ ] Admin panelga kirildi
- [ ] Namuna savollar import qilindi
- [ ] Faol test yaratildi
- [ ] Oddiy Telegram foydalanuvchisi bilan test yakunlandi
