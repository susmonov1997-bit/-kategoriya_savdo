-- Yillik savdo rejasi: yil × oy × kategoriya. Kategoriya nomi bo'yicha (name_key) bog'lanadi —
-- spravochnikda hali yo'q kategoriya rejasi ham saqlanadi, keyin avtomatik bog'lanadi.
CREATE TABLE IF NOT EXISTS sales_plan (
    year         INT  NOT NULL,
    month        INT  NOT NULL CHECK (month BETWEEN 1 AND 12),
    name_key     TEXT NOT NULL,
    category     TEXT NOT NULL,
    grp          TEXT,
    plan_amount  NUMERIC(20,2),          -- savdo summasi rejasi, so'm
    plan_gross   NUMERIC(20,2),          -- gross marja rejasi, so'm
    upload_id    INT,
    PRIMARY KEY (year, month, name_key)
);

ALTER TABLE uploads DROP CONSTRAINT IF EXISTS uploads_kind_check;
ALTER TABLE uploads ADD CONSTRAINT uploads_kind_check
    CHECK (kind IN ('products','branches','sales','bonus','owners','plan'));
