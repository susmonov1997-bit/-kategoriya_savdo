# Railway'ga chiqarish

Bitta servis: API + Mini App + bot bitta konteynerda (`python -m app.run`), alohida PostgreSQL.

## 1. Kodni GitHub'ga joylash
1. GitHub'da **private** repo oching (masalan `savdo-bot`).
2. Arxivdagi `savdo_bot` papkasi ichidagi hamma narsani repo ildiziga push qiling
   (`Dockerfile`, `railway.json`, `app/`, `sql/`, `webapp/` … ildizda turishi kerak).
   `.env` va Excel fayllar `.gitignore` da — repoga tushmaydi.

## 2. Railway loyihasi
1. railway.com → **New Project** → **Deploy from GitHub repo** → repo'ni tanlang.
2. Loyiha ichida **+ Create → Database → PostgreSQL**.
3. Servis (bot) → **Variables**:

| O'zgaruvchi | Qiymat |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `BOT_TOKEN` | BotFather bergan token |
| `ADMIN_IDS` | Telegram ID lar, vergul bilan (o'zingiznikini @userinfobot dan bilasiz) |
| `WEBAPP_URL` | 4-qadamdan keyin: `https://<domen>/app/` |

`API_DEV_USER_ID` ni Railway'ga **qo'shmang** (qo'shilsa ham Railway'da ishlamaydi).

4. Servis → **Settings → Networking → Generate Domain** → masalan `savdo-production.up.railway.app`.
   So'ng `WEBAPP_URL=https://savdo-production.up.railway.app/app/` qo'shing — servis qayta ishga tushadi.

## 3. Tekshirish
- **Deploy Logs** da: `Migratsiyalar: [...]`, `Uvicorn running on http://0.0.0.0:…`, `Start polling`.
- Brauzerda `https://<domen>/api/health` → `{"ok":true,"db":true}`.
- Botda `/start` → "📊 Savdoni ochish" tugmasi; chat pastida "Savdo" menyu tugmasi.

## 4. Ma'lumotni yuklash (botga, shu tartibda)
1. `хусусиятлар.xlsx` — tovar spravochnigi
2. `Filial spravochnigi.xlsx`
3. `Qoshimcha_daromad_shablon.xlsx` (to'ldirilgan foizlar)
4. `Chiqim_01-28.09_bot_uchun.xlsx` — sentyabr tarixi (39 MB li asl fayldan kerakli ustunlar va
   spravochnik kategoriyalari ajratilgan; Telegram 20 MB dan katta faylni qabul qilmaydi)

Keyin har kuni kunlik savdo faylini botga tashlaysiz. `/status` — bazadagi davr va oxirgi yuklashlar.

## Muhim
- Servis **1 nusxada** ishlashi shart (`numReplicas: 1`) — bot long-polling bilan ishlaydi.
- Shu tokenli botni bir vaqtda kompyuterda ham ishga tushirmang — Telegram "Conflict" xatosini beradi.
- Kod GitHub'ga push qilinsa Railway avtomatik qayta deploy qiladi; migratsiyalar o'zi qo'llanadi.
- Katta tarixiy fayllar uchun muqobil: kompyuterda `DATABASE_URL` ga Railway Postgres'ning
  **public** URL'ini (`DATABASE_PUBLIC_URL`) qo'yib `python -m scripts.load_file <fayl>` ishlatish.
