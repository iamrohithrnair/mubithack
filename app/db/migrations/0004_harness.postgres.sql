-- The decision harness: planted workstreams, the cases callers send, immutable
-- decisions, and the answers and outcomes appended to them. No business system
-- table is touched: the harness never executes anything.
CREATE TABLE IF NOT EXISTS workstreams (
    id                  TEXT PRIMARY KEY,
    contract            TEXT NOT NULL,
    version             INTEGER NOT NULL DEFAULT 1,
    audience            TEXT NOT NULL DEFAULT 'team',
    planted_by          TEXT NOT NULL,
    planted_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS harness_cases (
    workstream          TEXT NOT NULL,
    case_ref            TEXT NOT NULL,
    facts               TEXT NOT NULL DEFAULT '{}',
    text                TEXT,
    source              TEXT NOT NULL DEFAULT 'caller',
    revision            TEXT,
    received_at         TEXT NOT NULL,
    PRIMARY KEY (workstream, case_ref)
);

CREATE TABLE IF NOT EXISTS harness_decisions (
    decision_id         TEXT PRIMARY KEY,
    workstream          TEXT NOT NULL,
    case_ref            TEXT NOT NULL,
    idempotency_key     TEXT NOT NULL,
    input_hash          TEXT NOT NULL,
    verdict             TEXT NOT NULL,
    precedent_id        TEXT,
    precedent_version   INTEGER,
    actions             TEXT NOT NULL DEFAULT '[]',
    reasons             TEXT NOT NULL DEFAULT '[]',
    checks              TEXT NOT NULL DEFAULT '[]',
    model_calls         TEXT NOT NULL DEFAULT '[]',
    task_key            TEXT,
    caller              TEXT NOT NULL DEFAULT 'api',
    trace_id            TEXT NOT NULL,
    resolves            TEXT,
    source_revision     TEXT,
    created_at          TEXT NOT NULL,
    expires_at          TEXT,
    UNIQUE (workstream, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_harness_decisions_ws ON harness_decisions(workstream, verdict);
CREATE INDEX IF NOT EXISTS idx_harness_decisions_case ON harness_decisions(workstream, case_ref);

CREATE TABLE IF NOT EXISTS harness_answers (
    answer_id           TEXT PRIMARY KEY,
    decision_id         TEXT NOT NULL,
    answer              TEXT NOT NULL,
    remembered          BOOLEAN NOT NULL DEFAULT FALSE,
    precedent_id        TEXT,
    actor               TEXT NOT NULL,
    note                TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_harness_answers_decision ON harness_answers(decision_id);

CREATE TABLE IF NOT EXISTS harness_outcomes (
    outcome_id          TEXT PRIMARY KEY,
    decision_id         TEXT NOT NULL,
    status              TEXT NOT NULL,
    detail              TEXT NOT NULL DEFAULT '',
    actor               TEXT NOT NULL,
    verification        TEXT NOT NULL DEFAULT 'reported',
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_harness_outcomes_decision ON harness_outcomes(decision_id);
