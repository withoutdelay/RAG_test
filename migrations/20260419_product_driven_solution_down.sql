-- Manual rollback companion for product-driven foundation release rehearsal.
-- Mirrors the downgrade intent from backend/alembic/versions/20260419_0005 and 20260419_0006.

BEGIN;

ALTER TABLE IF EXISTS solution_snapshots
  DROP COLUMN IF EXISTS source_catalog_version;

DROP TABLE IF EXISTS product_constraints;
DROP TABLE IF EXISTS product_standard_configs;
DROP TABLE IF EXISTS product_series;
DROP TABLE IF EXISTS solution_snapshots;

COMMIT;
