-- =====================================================================
-- 013_matrix.sql — Google Sheet «Матрица КБТ» (varaq «Matritsa»): filial × SKU → Turishi (min dona)
-- Har kuni 06:00 da (va /matritsa bilan) to'liq almashtiriladi. Sheet o'qilmasa — oldingisi qoladi.
-- =====================================================================

-- Joriy matritsa (oxirgi muvaffaqiyatli o'qish)
CREATE TABLE IF NOT EXISTS matrix_rows (
    id            SERIAL PRIMARY KEY,
    region        TEXT,            -- «Xudud»
    branch_name   TEXT NOT NULL,   -- «Filial»
    branch_ext_id TEXT,            -- «Filial ID»
    format        TEXT,            -- «Format»
    cluster       TEXT,            -- «Klaster»
    category      TEXT NOT NULL,   -- «Категория»
    vitrina_cap   NUMERIC(10,2),   -- «Vitrina sig'imi» (filial × kategoriya)
    sub0          TEXT,            -- «Подкатегория»
    sub1          TEXT,            -- «1-Субкатегория»
    sub2          TEXT,            -- «2-Субкатегория»
    sub3          TEXT,            -- «3-Субкатегория»
    product_name  TEXT NOT NULL,   -- «Tovar nomi»
    brand         TEXT,            -- «Brend»
    need_qty      NUMERIC(10,2) NOT NULL,   -- «Turishi»
    branch_id     INT,             -- bog'langan filial (NULL — topilmadi)
    product_id    BIGINT,          -- bog'langan SKU (NULL — topilmadi)
    match_tier    SMALLINT,        -- 1 — nom aynan mos, 2 — taxminiy (bo'shliq/belgi/harf farqi)
    category_id   INT
);
CREATE INDEX IF NOT EXISTS ix_matrix_rows_branch  ON matrix_rows (branch_id);
CREATE INDEX IF NOT EXISTS ix_matrix_rows_product ON matrix_rows (product_id);

-- Har bir o'qish jurnali (06:00, /matritsa)
CREATE TABLE IF NOT EXISTS matrix_syncs (
    id           SERIAL PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    trigger      TEXT NOT NULL,            -- 'auto' | 'manual'
    started_by   BIGINT,
    status       TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','ok','failed')),
    rows         INT,
    error        TEXT,
    stats        JSONB
);
