-- =====================================================================
-- 001_products.sql — Tovar spravochnigi, xususiyatlar, yuklashlar jurnali
-- Qayta ishga tushirish xavfsiz (IF NOT EXISTS / ON CONFLICT).
-- =====================================================================

-- Kategoriyalar (Группа → Категория)
CREATE TABLE IF NOT EXISTS categories (
    id          SERIAL PRIMARY KEY,
    grp         TEXT NOT NULL,                 -- Группа (masalan, КБТ)
    name        TEXT NOT NULL UNIQUE,          -- Категория (masalan, Совутгичлар)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Har bir kategoriya uchun xususiyat darajalari.
-- slot: 0 = Подкатегория, 1..N = "N-Субкатегория".
-- Nom/tur/tartib shu yerda — yangi kategoriya yoki xususiyat uchun kod o'zgarmaydi.
CREATE TABLE IF NOT EXISTS category_attributes (
    category_id  INT      NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    slot         SMALLINT NOT NULL,
    source_col   TEXT     NOT NULL,            -- Excel'dagi ustun nomi
    name         TEXT     NOT NULL,            -- Mini App'da ko'rinadigan nom
    data_type    TEXT     NOT NULL DEFAULT 'text' CHECK (data_type IN ('text','number')),
    unit         TEXT,                          -- sm, l ...
    sort_order   SMALLINT NOT NULL DEFAULT 0,
    is_filter    BOOLEAN  NOT NULL DEFAULT TRUE,
    PRIMARY KEY (category_id, slot)
);

-- Tovarlar (SKU). Kalit — "Товар Ид".
-- attrs: {"a0": "Но фрост", "a1": 185, "a2": 252, "a3": 98, "a4": "White"}
CREATE TABLE IF NOT EXISTS products (
    product_id   BIGINT PRIMARY KEY,           -- Товар Ид
    name         TEXT   NOT NULL,              -- Товар номи
    category_id  INT    NOT NULL REFERENCES categories(id),
    brand        TEXT   NOT NULL,              -- Бренд
    status       TEXT,                          -- Статус (матричный, Тест, ...)
    attrs        JSONB  NOT NULL DEFAULT '{}'::jsonb,
    upload_id    INT,                           -- oxirgi marta qaysi yuklashda yangilangan
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_products_category ON products(category_id);
CREATE INDEX IF NOT EXISTS ix_products_brand    ON products(category_id, brand);
CREATE INDEX IF NOT EXISTS ix_products_attrs    ON products USING GIN (attrs jsonb_path_ops);

-- Yuklashlar jurnali (tovar va savdo fayllari uchun umumiy)
CREATE TABLE IF NOT EXISTS uploads (
    id           SERIAL PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('products','branches','sales')),
    file_name    TEXT,
    tg_user_id   BIGINT,
    status       TEXT NOT NULL DEFAULT 'started' CHECK (status IN ('started','done','failed')),
    rows_total   INT,
    rows_loaded  INT,
    report       JSONB,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);

-- ---------------------------------------------------------------------
-- Tasdiqlangan xususiyat nomlari (keyin UPDATE bilan o'zgartirish mumkin)
-- ---------------------------------------------------------------------
INSERT INTO categories (grp, name) VALUES
    ('КБТ','Совутгичлар'), ('КБТ','Музлатгичлар'), ('КБТ','Кулерлар')
ON CONFLICT (name) DO NOTHING;

INSERT INTO category_attributes (category_id, slot, source_col, name, data_type, unit, sort_order)
SELECT c.id, v.slot, v.source_col, v.name, v.data_type, v.unit, v.slot
FROM categories c
JOIN (VALUES
    ('Совутгичлар', 0, 'Подкатегория',   'Turi',              'text',   NULL),
    ('Совутгичлар', 1, '1-Субкатегория', 'Balandlik',         'number', 'sm'),
    ('Совутгичлар', 2, '2-Субкатегория', 'Umumiy hajm',       'number', 'l'),
    ('Совутгичлар', 3, '3-Субкатегория', 'Muzlatkich hajmi',  'number', 'l'),
    ('Совутгичлар', 4, '4-Субкатегория', 'Rang',              'text',   NULL),
    ('Музлатгичлар',0, 'Подкатегория',   'Turi',              'text',   NULL),
    ('Музлатгичлар',1, '1-Субкатегория', 'Hajm',              'number', 'l'),
    ('Музлатгичлар',2, '2-Субкатегория', 'O''lcham',          'text',   NULL),
    ('Кулерлар',    0, 'Подкатегория',   'Suv yuklanishi',    'text',   NULL),
    ('Кулерлар',    1, '1-Субкатегория', 'Suv turlari',       'text',   NULL),
    ('Кулерлар',    2, '2-Субкатегория', 'Choynak',           'text',   NULL)
) AS v(cat, slot, source_col, name, data_type, unit) ON v.cat = c.name
ON CONFLICT (category_id, slot) DO NOTHING;
