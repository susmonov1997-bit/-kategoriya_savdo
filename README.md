# Savdo Qirqim — Telegram bot + Mini App

Kategoriyalar bo'yicha kunlik savdoni hudud, klaster, filial, xususiyat, brend va SKU kesimida ko'rsatadi.
Barcha fayllar bitta papkada (papka ichida papka yo'q).

## Fayllar
| Fayl | Vazifasi |
|---|---|
| `run.py` | Ishga tushirish: API + Mini App + bot bitta jarayonda (Railway shu faylni ishlatadi) |
| `bot.py` | Telegram bot: Excel qabul qilish, /start, /status, /attrs |
| `api_main.py`, `api_auth.py`, `api_queries.py`, `api_schemas.py` | FastAPI: hisob-kitoblar, Telegram kirish tekshiruvi |
| `loaders_*.py` | Excel yuklovchilar: tovar, filial, savdo, qo'shimcha daromad % |
| `access.py`, `004_users.sql` | Kirish: so'rov → admin tasdiqlaydi (👁 Ko'ruvchi / 📥 Yuklovchi) yoki rad etadi; /users |
| `db.py`, `config.py` | Baza ulanishi, migratsiyalar, sozlamalar |
| `001_…sql`, `002_…sql`, `003_…sql` | Baza jadvallari (startda avtomatik qo'llanadi) |
| `index.html`, `app.css`, `app.js` | Mini App (`/app/` manzilida): Tahlil va 📥 Yuklash bo'limlari |
| `api_upload.py` | Mini App orqali fayl yuklash: tekshirish → tasdiqlash (100 MB gacha, Telegram cheklovisiz) |
| `load_file.py` | Botsiz yuklash (katta tarixiy fayllar uchun) |
| `make_bonus_template.py` | Brend × kategoriya % shablonini yaratish |
| `Dockerfile`, `railway.json`, `requirements.txt` | Railway deploy |

## Lokal ishga tushirish
```bash
pip install -r requirements.txt
cp .env.example .env        # BOT_TOKEN, DATABASE_URL, ADMIN_IDS
python run.py               # http://localhost:8000/app/
```
Botsiz yuklash: `python load_file.py хусусиятлар.xlsx "Filial spravochnigi.xlsx" savdo.xlsx`

## Hisob qoidalari
- Savdo = Жами(Чиқим нархи); Front marja = savdo − Жами(Кирим нархи)
- Qo'shimcha daromad = Жами(Кирим нархи) × brend % ; Gross marja = Front marja + qo'shimcha daromad
- Бонус qatorlar donaga qo'shiladi; o'rtacha narx bonussiz dona bo'yicha
- Faqat spravochnikdagi kategoriyalar; "К"/"M"/"Z" va qaytarishlar hisobga olinmaydi
- Bir sana qayta yuklansa, o'sha kun to'liq almashtiriladi

Deploy: `DEPLOY.md`.
