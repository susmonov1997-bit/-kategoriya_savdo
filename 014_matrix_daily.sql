-- 014_matrix_daily.sql — matritsa bajarilishi tarixi (▲▼ «oldingi kunga») + kesh yangilanishi.
-- Kalit — filial qoldig'i sanasi: har yangi qoldiq (yoki matritsa) bilan shu sana qatorlari qayta hisoblanadi.
CREATE TABLE IF NOT EXISTS matrix_daily (
    snap_date    DATE NOT NULL,      -- filial qoldig'i sanasi
    branch_id    INT  NOT NULL,
    category_id  INT  NOT NULL,
    pos          INT  NOT NULL,      -- matritsa pozitsiyalari (bog'langan)
    ok           INT  NOT NULL,      -- qoldiq ≥ turishi
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (snap_date, branch_id, category_id)
);

-- matritsa yangilansa API keshi ham yangilansin
DROP TRIGGER IF EXISTS trg_fact_bump ON matrix_rows;
CREATE TRIGGER trg_fact_bump AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON matrix_rows
    FOR EACH STATEMENT EXECUTE FUNCTION fact_bump_version();
