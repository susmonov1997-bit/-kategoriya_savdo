"""Ta'minotchilar (022_suppliers.sql): kirim nomlaridan ro'yxat, guruh taklifi va tovar → ta'minotchi hisobi.

Tovarning ta'minotchisi (ustuvorlik bo'yicha):
  x — tovar istisnosi (sup_product, qo'lda)
  r — qoida: brend + kategoriya → ta'minotchi (sup_rules, qo'lda)
  k — tovarning o'z kirim tarixi (eng ko'p dona kelgan ta'minotchi)
  a — avtomatik qoida: shu brend + kategoriya kirimida eng katta summa (kirimi yo'q yangi tovar ham oladi)
"""
from __future__ import annotations

import logging
import re

import asyncpg

log = logging.getLogger(__name__)

# guruh nomini topishda tashlab yuboriladigan so'zlar: shaharlar, yo'nalish, tur
_STOP = {
    "тошкент", "ташкент", "toshkent", "tashkent", "самарқанд", "самарканд", "samarqand", "samarkand",
    "андижон", "андижан", "фарғона", "фаргона", "наманган", "бухоро", "бухара", "хоразм", "урганч", "нукус",
    "қарши", "карши", "термиз", "навоий", "жиззах", "гулистон", "қўқон", "коканд", "дубай", "dubay", "dubai",
    "кбт", "мбт", "kbt", "mbt", "импорт", "import", "экспорт", "смартфон", "смартфоны", "smartfon", "мелкий",
    "мелкая", "техника", "ооо", "мчж", "mchj", "xk", "хк", "ok", "ltd", "llc", "group", "груп", "группа",
    "таъминотчи", "таьминотчи", "поставщик",
}
_TOK = re.compile(r"[\w\-]+", re.U)
# кирилл/лотин бир хил кўринадиган ҳарфлар («Tошкент» — лотин T)
_LAT2CYR = str.maketrans("aceopxyACEOPXYTHKMB", "асеорхуАСЕОРХУТНКМВ")


def _norm(t: str) -> str:
    low = t.lower()
    if any("а" <= ch <= "я" or ch in "ўқғҳё" for ch in low):      # kirill so'zdagi lotin harflar
        low = t.translate(_LAT2CYR).lower()
    return low


def group_name(supplier: str) -> str:
    """«Тошкент Artel КБТ» → Artel, «LG Импорт Стиральная машина» → LG, «Пгс тошкент» → ПГС."""
    toks = [t for t in _TOK.findall(supplier or "") if _norm(t) not in _STOP and not t.isdigit()]
    if not toks:
        return (supplier or "—").strip() or "—"
    t = toks[0]
    if len(t) <= 4 and (t.islower() or t.istitle()):                # Пгс → ПГС, Бм → БМ
        t = t.upper()
    elif t.islower():
        t = t.capitalize()
    return t


async def sync(con: asyncpg.Connection) -> list[str]:
    """Kirimdagi yangi ta'minotchilarni qo'shadi va guruh taklif qiladi (tasdiqlanmagan). Yangi nomlar qaytadi."""
    names = [r[0] for r in await con.fetch(
        """SELECT DISTINCT supplier FROM (SELECT supplier FROM wh_receipts UNION SELECT supplier FROM receipt_pending) s
           WHERE supplier <> '' AND NOT EXISTS (SELECT 1 FROM suppliers x WHERE x.name = s.supplier)""")]
    if not names:
        return []
    groups = {r["name"].lower(): r["id"] for r in await con.fetch("SELECT id, name FROM sup_groups")}
    for n in sorted(names):
        g = group_name(n)
        gid = groups.get(g.lower())
        if gid is None:
            gid = await con.fetchval(
                "INSERT INTO sup_groups(name) VALUES ($1) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id", g)
            groups[g.lower()] = gid
        await con.execute("INSERT INTO suppliers(name, group_id) VALUES ($1, $2) ON CONFLICT (name) DO NOTHING", n, gid)
    return names


# kirim tarixi: tovar × ta'minotchi
_K = """k AS (SELECT r.product_id, s.id sid, sum(r.qty) q, coalesce(sum(r.value_usd), 0) u
              FROM wh_receipts r JOIN suppliers s ON s.name = r.supplier GROUP BY 1, 2)"""
# avtomatik qoida: kategoriya × brend → eng katta summa
_AR = """cb AS (SELECT p.category_id, COALESCE(p.brand, '—') brand, k.sid, sum(k.u) u, sum(k.q) q
               FROM k JOIN products p USING (product_id) GROUP BY 1, 2, 3),
         ar AS (SELECT DISTINCT ON (category_id, brand) category_id, brand, sid FROM cb
                ORDER BY category_id, brand, u DESC, q DESC, sid)"""


async def refresh(con: asyncpg.Connection) -> int:
    """product_supplier'ni qayta hisoblaydi (faqat o'zgarganlar yoziladi). O'zgargan qatorlar soni qaytadi."""
    r = await con.fetchrow(f"""
WITH {_K},
kp AS (SELECT DISTINCT ON (product_id) product_id, sid FROM k ORDER BY product_id, q DESC, u DESC, sid),
{_AR},
res AS (
    SELECT p.product_id,
           COALESCE(x.supplier_id, mr.supplier_id, kp.sid, ar.sid) sid,
           CASE WHEN x.supplier_id IS NOT NULL THEN 'x' WHEN mr.supplier_id IS NOT NULL THEN 'r'
                WHEN kp.sid IS NOT NULL THEN 'k' ELSE 'a' END src
    FROM products p
    LEFT JOIN sup_product x ON x.product_id = p.product_id
    LEFT JOIN sup_rules mr ON mr.category_id = p.category_id AND mr.brand = COALESCE(p.brand, '—')
    LEFT JOIN kp ON kp.product_id = p.product_id
    LEFT JOIN ar ON ar.category_id = p.category_id AND ar.brand = COALESCE(p.brand, '—')
),
fin AS (SELECT res.product_id, res.sid, s.group_id, res.src FROM res JOIN suppliers s ON s.id = res.sid),
up AS (INSERT INTO product_supplier AS t (product_id, supplier_id, group_id, src)
       SELECT * FROM fin
       ON CONFLICT (product_id) DO UPDATE SET supplier_id = EXCLUDED.supplier_id, group_id = EXCLUDED.group_id, src = EXCLUDED.src
       WHERE (t.supplier_id, t.group_id, t.src) IS DISTINCT FROM (EXCLUDED.supplier_id, EXCLUDED.group_id, EXCLUDED.src)
       RETURNING 1),
del AS (DELETE FROM product_supplier t WHERE NOT EXISTS (SELECT 1 FROM fin WHERE fin.product_id = t.product_id) RETURNING 1)
SELECT (SELECT count(*) FROM up) up, (SELECT count(*) FROM del) del""")
    return r["up"] + r["del"]


async def sync_and_refresh(con: asyncpg.Connection) -> dict:
    new = await sync(con)
    n = await refresh(con)
    return {"new": new, "changed": n}


async def safe_refresh(pool_or_con) -> dict | None:
    """Yuklashdan keyin: xato bo'lsa ham asosiy ish to'xtamaydi."""
    try:
        if hasattr(pool_or_con, "acquire"):          # pool (yoki tekshiruvdagi bitta ulanishli pool)
            async with pool_or_con.acquire() as con:
                return await sync_and_refresh(con)
        return await sync_and_refresh(pool_or_con)
    except Exception:  # noqa: BLE001
        log.exception("Ta'minotchilar yangilanmadi")
        return None
