-- ============================================================================
-- Reverse migration 007 — drop the statute feed state table + id sequence.
-- ----------------------------------------------------------------------------
-- Does NOT touch corpus_* rows the feed may have inserted; removing those is a
-- separate, deliberate cleanup (DELETE WHERE id >= 9000000000), not part of
-- tearing down the feed's bookkeeping.
-- ============================================================================

BEGIN;

DROP SEQUENCE IF EXISTS corpus_feed_id_seq;
DROP TABLE IF EXISTS statute_feed_state;

COMMIT;
