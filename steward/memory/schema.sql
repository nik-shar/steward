-- ===========================================================================
-- Steward memory schema
--
-- One file, applied with CREATE IF NOT EXISTS, recorded in schema_version.
-- The design rule behind the tables below:
--
--   * Records are append-only. A correction is a NEW row plus a pointer, so a
--     bad write is recoverable and the history stays auditable.
--   * Confidence ceilings are stored per row, not just enforced in code, so a
--     later reader can tell what the store believed at write time.
--   * The audit log is deliberately NOT here. It lives in audit.jsonl, because
--     an audit trail must not live inside the thing it is auditing.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL,
    applied_at INTEGER NOT NULL
);

-- ---------------------------------------------------------------------------
-- Organic memory ("DNA"): what Steward believes about the person, and how sure
-- it is. APPEND-ONLY. `superseded_by IS NULL` means "currently believed".
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dna_memory (
    id            TEXT PRIMARY KEY,
    text          TEXT NOT NULL,
    kind          TEXT NOT NULL,   -- fact | preference | goal | observation | relationship
    confidence    REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    ceiling       REAL NOT NULL,   -- the ceiling in force for `source` at write time
    source        TEXT NOT NULL,   -- user_stated | tool | reflection | inferred
    evidence      TEXT,            -- why this is believed
    created_at    INTEGER NOT NULL,-- unix ms
    superseded_by TEXT REFERENCES dna_memory(id),  -- NULL = still believed
    supersedes    TEXT REFERENCES dna_memory(id)   -- the row this one corrects
);

CREATE INDEX IF NOT EXISTS dna_memory_current_idx
    ON dna_memory (superseded_by, created_at DESC);
CREATE INDEX IF NOT EXISTS dna_memory_kind_idx
    ON dna_memory (kind, superseded_by);

-- Lexical recall. Plain (not external-content) FTS5 keeps the sync rule simple:
-- dna_memory is append-only, so one insert trigger is enough and text can never
-- drift out of the index.
CREATE VIRTUAL TABLE IF NOT EXISTS dna_memory_fts USING fts5(
    id UNINDEXED,
    text,
    kind UNINDEXED,
    tokenize = 'porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS dna_memory_fts_ai
AFTER INSERT ON dna_memory BEGIN
    INSERT INTO dna_memory_fts (id, text, kind)
    VALUES (new.id, new.text, new.kind);
END;

-- Only what is currently believed should be recallable.
CREATE VIEW IF NOT EXISTS current_dna_memory AS
    SELECT * FROM dna_memory WHERE superseded_by IS NULL;

-- ---------------------------------------------------------------------------
-- Profile facts: the small, stable picture of who the person is.
--
-- Unlike dna_memory this is a KEY/VALUE projection, so updating in place is
-- correct — the append-only record of how it changed lives in dna_memory.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS profile_facts (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    source     TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

-- ---------------------------------------------------------------------------
-- Episodic events: what happened, and when. Chronological, append-only.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS episodic_events (
    id      TEXT PRIMARY KEY,
    at      INTEGER NOT NULL,   -- unix ms
    kind    TEXT NOT NULL,      -- turn | tool | delegation | system
    summary TEXT NOT NULL,
    detail  TEXT                -- JSON, optional
);

CREATE INDEX IF NOT EXISTS episodic_events_at_idx ON episodic_events (at DESC);

-- ---------------------------------------------------------------------------
-- Day slots: the calendar grid. Reserved for the `calendar` pack (M2) — 48
-- half-hour slots per day, wall-clock readings in STEWARD-timezone terms.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS day_slots (
    day        TEXT NOT NULL,   -- YYYY-MM-DD, local calendar day
    slot       INTEGER NOT NULL CHECK (slot >= 0 AND slot < 48),
    label      TEXT NOT NULL,
    source     TEXT NOT NULL,   -- steward | user | import
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (day, slot)
);
