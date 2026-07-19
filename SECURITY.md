# Security Policy

## Maxfiy ma’lumotlar

Quyidagilarni GitHub repository, screenshot yoki loglarda oshkor qilmang:

- `BOT_TOKEN`
- `SECRET_KEY`
- `WEBHOOK_SECRET`
- `DATABASE_URL`
- `BOOTSTRAP_ADMIN_PASSWORD`

Token oshkor bo‘lsa, BotFather orqali darhol yangilang. DB paroli oshkor bo‘lsa, ma’lumotlar bazasi provayderida parolni almashtiring.

## Production sozlamalari

```env
DEBUG=false
ENABLE_DEV_AUTH=false
```

`ALLOWED_ORIGINS` va `TRUSTED_HOSTS` ni faqat o‘z domeningiz hamda zarur Render hostlari bilan cheklang.

## Zaxira

PostgreSQL provayderining backup/restore imkoniyatlarini yoqing. Savollar bazasini va muhim natijalarni davriy eksport qiling.
