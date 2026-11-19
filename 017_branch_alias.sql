-- 017_branch_alias.sql — matritsadagi filial nomi boshqacha yozilgan bo'lsa, botdagi mavjud filialga bog'lash.
CREATE TABLE IF NOT EXISTS branch_aliases (
    alias_key   TEXT PRIMARY KEY,                -- name_key(matritsadagi nom)
    branch_id   INT  NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    created_by  BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE branch_requests ADD COLUMN IF NOT EXISTS cluster_hint TEXT;   -- matritsadagi klaster
ALTER TABLE branch_requests ADD COLUMN IF NOT EXISTS src TEXT;            -- 'mx' — matritsadan
