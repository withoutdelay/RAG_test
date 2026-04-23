-- Rollback companion for the release-hardening no-op migration.
-- Because the forward migration does not mutate schema or data, rollback is a no-op.

BEGIN;
SELECT 1;
COMMIT;
