# Savdo Mini App Bot — 1-bosqich (baza, yuklash) va 2-bosqich (API)

## O'rnatish
```bash
pip install -r requirements.txt
cp .env.example .env        # BOT_TOKEN, DATABASE_URL, ADMIN_IDS
python -m app.bot.main      # migratsiyalar avtomatik qo'llanadi, bot ishga tushadi
```

## Boshlang'ich yuklash (tartib muhim)
```bash
python -m scripts.load_file хусусиятлар.xlsx "Filial spravochnigi.xlsx" "Chiqim tovarlar 01.09 - 28.09.2026.xlsx"
python -m scripts.load_file Qoshimcha_daromad.xlsx     # istalgan vaqtda, qayta yuklash mumkin
```
Oylik fayl 39 MB — Telegram bot 20 MB dan katta faylni qabul qilmaydi, shuning uchun tarix skript bilan,
kunlik fayllar esa botga tashlanadi. Fayl turi sarlavhalar bo'yicha avtomatik aniqlanadi.

## Bot buyruqlari
- Excel fayl tashlash — savdo / tovar spravochnigi / filial spravochnigi
- `/status` — bazadagi davr, SKU, filiallar, oxirgi yuklashlar
- `/attrs` — kategoriyalar va xususiyatlar

## Qoidalar (kelishilgan)
- Savdo = Жами(Чиқим нархи); marja = Жами(Чиқим нархи) − Жами(Кирим нархи)
- Бонус qatorlar donaga qo'shiladi, tannarxi marjaga kiradi (is_bonus bilan alohida saqlanadi)
- Faqat spravochnikdagi kategoriyalar; "К"/"M"/"Z" va qaytarishlar hisobga olinmaydi
- Spravochnikda yo'q SKU/filial yuklanmaydi, ro'yxati hisobotda chiqadi
- Fayldagi sanalar bazada to'liq almashtiriladi (dublikat yo'q)
- Mijoz/xodim/shartnoma ma'lumotlari saqlanmaydi
- Qo'shimcha daromad (dop doxod + retro + kompensatsiya): brend × kategoriya bo'yicha bitta %.
  Qo'shimcha daromad = Жами(Кирим нархи) × %; Marja = Valovka + qo'shimcha daromad.
  Kategoriya bo'sh → brendning barcha kategoriyalari. Fayl har yuklashda to'liq almashtiriladi,
  % hisob paytida qo'llanadi (view `sales_enriched`), ya'ni tarix ham yangi % bilan hisoblanadi.

## Tuzilma
- `sql/001_products.sql` — categories, category_attributes, products (attrs JSONB), uploads
- `sql/002_branches_sales.sql` — regions, clusters, branches, sales_daily, attribute_ranges
- `sql/003_brand_bonus.sql` — brand_bonus, view'lar product_bonus_pct va sales_enriched
- `app/loaders/` — common (Excel o'qish), products, branches, sales, dispatch (turini aniqlash)
- `app/bot/main.py` — aiogram bot
- `scripts/load_file.py` — botsiz yuklash
- `scripts/make_bonus_template.py` — brend × kategoriya % shablonini bazadan yaratish

## Kodsiz sozlash
```sql
-- xususiyat nomi
UPDATE category_attributes SET name='Rang' WHERE category_id=… AND slot=4;
-- diapazon chegarasi: (lo, hi]
UPDATE attribute_ranges SET hi=160, label='≤160 sm' WHERE category_id=… AND slot=1 AND sort_order=1;
```

## 2-bosqich: API (FastAPI)
```bash
uvicorn app.api.main:app --host 0.0.0.0 --port 8000
# hujjat: http://localhost:8000/api/docs
# lokal sinov (Telegram'siz): API_DEV_USER_ID=<ADMIN_IDS dagi ID> ni .env ga yozing
```
Autentifikatsiya: `Authorization: tma <Telegram.WebApp.initData>` — imzo BOT_TOKEN bilan tekshiriladi,
foydalanuvchi ADMIN_IDS da bo'lishi kerak.

| Endpoint | Vazifasi |
|---|---|
| `GET /api/meta` | kategoriyalar, xususiyatlar va diapazonlar, ma'lumot davri, drill-down tartibi |
| `POST /api/options` | filtr qiymatlari (faceted), joriy davr savdosi bilan |
| `POST /api/summary` | KPI: savdo, dona, valovka, qo'shimcha daromad, marja, o'rtacha narx + solishtirish |
| `POST /api/daily` | kunlik dinamika, solishtirma davr kunma-kun |
| `POST /api/breakdown` | qirqim: `rows` (+ `cols` kesma), ulush %, o'zgarish, saralash, limit |

Umumiy filtr (JSON): `category_id, date_from, date_to, compare (prev|yoy|none), region_ids, cluster_ids,
branch_ids, brands, statuses, product_ids, attrs {"slot": ["qiymat"]}`.
O'lchovlar: `region, cluster, branch, brand, status, sku, attr:<slot>`.
Drill-down: joy — region → cluster → branch; tovar — attr:0 → attr:1 … → brand → sku
(Mini App qator bosilganda qiymatni filtrga qo'shib, keyingi o'lchovni so'raydi).

Hisob qoidalari: o'rtacha narx = savdo / sotilgan dona (bonus donalarsiz); ulush — joriy filtrlar
jamisiga nisbatan; o'zgarish — summalar uchun %, marja% uchun foiz punkti (pp);
`prev` = oldingi teng davr, `yoy` = o'tgan yilning shu sanalari.

## 3-bosqich: Mini App (ishchi)
- Fayllar: `webapp/app/index.html`, `app.css`, `app.js` — API bilan bir serverda `/app/` manzilida beriladi
  (`uvicorn app.api.main:app` ishga tushganda). `/` → `/app/` ga yo'naltiradi.
- Telegram: `.env` da `WEBAPP_URL=https://<domen>/app/` → bot /start da "📊 Savdoni ochish" tugmasi va chat
  menyusida "Savdo" tugmasi paydo bo'ladi. Telegram Mini App faqat HTTPS bilan ochiladi (5-bosqich — Railway).
- Kirish: Mini App `Telegram.WebApp.initData` ni `Authorization: tma …` sarlavhasida yuboradi; ruxsat yo'q bo'lsa
  foydalanuvchi o'z Telegram ID sini ko'radi.
- Lokal sinov (Telegram'siz): `.env` ga `API_DEV_USER_ID=<ADMIN_IDS dagi ID>`, so'ng brauzerda http://localhost:8000/app/
- Ekran: kategoriya, davr (Kecha/7/14/30 kun/Oy boshidan/sana), solishtirish, filtrlar, KPI, kunlik dinamika,
  qirqim (ulush kartasi, jadval, kesma, drill-down Hudud → Klaster → Filial → xususiyatlar → Brend → SKU,
  Telegram "Orqaga" tugmasi bilan), xususiyatlar bloklari, TOP-10 SKU.

## 5-bosqich: Deploy (Railway)
Batafsil: `DEPLOY.md`. Qisqacha: Dockerfile + railway.json; bitta servis `python -m app.run` (API + Mini App + bot),
Railway PostgreSQL; o'zgaruvchilar: DATABASE_URL, BOT_TOKEN, ADMIN_IDS, WEBAPP_URL.
