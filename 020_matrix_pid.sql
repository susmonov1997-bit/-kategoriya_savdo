-- 020_matrix_pid.sql — matritsada «Товар ИД» ustuni: tovar avval ID bo'yicha bog'lanadi.
ALTER TABLE matrix_rows ADD COLUMN IF NOT EXISTS product_ext BIGINT;   -- varaqdagi Товар ИД
ALTER TABLE matrix_rows ADD COLUMN IF NOT EXISTS id_note TEXT;         -- 'mismatch' — ID topildi, lekin nomi butunlay boshqa
CREATE INDEX IF NOT EXISTS ix_matrix_rows_ext ON matrix_rows (product_ext) WHERE product_id IS NULL;
