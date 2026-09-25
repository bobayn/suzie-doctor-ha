PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS doctor_v2_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS doctor_v2_role_slots (
    slot_id TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK(role IN ('FIELD_SUZIE','HOUSE','WILSON')),
    reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved IN (0,1)),
    state TEXT NOT NULL DEFAULT 'FREE' CHECK(state IN ('FREE','BUSY','DISABLED')),
    assignment_id TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS doctor_v2_role_targets (
    role TEXT PRIMARY KEY CHECK(role IN ('FIELD_SUZIE','HOUSE','WILSON')),
    project_id TEXT,
    project_url TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS doctor_v2_patient_cards (
    patient_id TEXT PRIMARY KEY,
    card_version INTEGER NOT NULL DEFAULT 1,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS doctor_v2_patient_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('EVENT','OBSERVATION','INCIDENT','CASE_EVENT')),
    source TEXT NOT NULL,
    severity TEXT,
    fingerprint TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(patient_id) REFERENCES doctor_v2_patient_cards(patient_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS doctor_v2_patient_events_patient_idx
    ON doctor_v2_patient_events(patient_id, event_id);
CREATE INDEX IF NOT EXISTS doctor_v2_patient_events_fingerprint_idx
    ON doctor_v2_patient_events(patient_id, fingerprint, event_id);

CREATE TABLE IF NOT EXISTS doctor_v2_house_jobs (
    house_job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL,
    trigger_event_id INTEGER,
    card_version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'WAITING' CHECK(status IN ('WAITING','CLAIMED','DONE','CANCELLED')),
    priority INTEGER NOT NULL DEFAULT 50,
    claimed_dialog_id TEXT,
    claimed_at TEXT,
    scheduler_yield_until TEXT,
    scheduler_yield_count INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(patient_id, trigger_event_id),
    FOREIGN KEY(patient_id) REFERENCES doctor_v2_patient_cards(patient_id) ON DELETE CASCADE,
    FOREIGN KEY(trigger_event_id) REFERENCES doctor_v2_patient_events(event_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS doctor_v2_house_jobs_status_idx
    ON doctor_v2_house_jobs(status, priority DESC, house_job_id);

CREATE TABLE IF NOT EXISTS doctor_v2_house_decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    house_job_id INTEGER NOT NULL UNIQUE,
    patient_id TEXT NOT NULL,
    card_version INTEGER NOT NULL,
    finding_class TEXT NOT NULL CHECK(finding_class IN ('EVENT','OBSERVATION','INCIDENT','CASE')),
    significance TEXT NOT NULL CHECK(significance IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    decision TEXT NOT NULL CHECK(decision IN ('OBSERVE','RECHECK_LATER','IGNORE_AS_NOISE','HUMAN_ACTION_REQUIRED','DISPATCH_SUZIE')),
    field_priority TEXT CHECK(field_priority IN ('LOW','NORMAL','HIGH','URGENT')),
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(house_job_id) REFERENCES doctor_v2_house_jobs(house_job_id) ON DELETE CASCADE,
    FOREIGN KEY(patient_id) REFERENCES doctor_v2_patient_cards(patient_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS doctor_v2_field_queue (
    queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL,
    source_house_decision_id INTEGER NOT NULL,
    legacy_case_id INTEGER,
    priority INTEGER NOT NULL DEFAULT 50,
    status TEXT NOT NULL DEFAULT 'WAITING' CHECK(status IN ('WAITING','ASSIGNED','CLAIMED','DONE','CANCELLED')),
    assignment_id TEXT,
    house_directive TEXT CHECK(house_directive IS NULL OR house_directive='VALIDATE_FIRST'),
    experimental_protocol_id TEXT,
    validation_stage TEXT CHECK(validation_stage IS NULL OR validation_stage IN ('0/3','1/3','2/3')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_house_decision_id),
    FOREIGN KEY(patient_id) REFERENCES doctor_v2_patient_cards(patient_id) ON DELETE CASCADE,
    FOREIGN KEY(source_house_decision_id) REFERENCES doctor_v2_house_decisions(decision_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS doctor_v2_field_queue_status_idx
    ON doctor_v2_field_queue(status, priority DESC, queue_id);

CREATE TABLE IF NOT EXISTS doctor_v2_web_dialogs (
    dialog_id TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK(role IN ('FIELD_SUZIE','HOUSE','WILSON')),
    assignment_id TEXT NOT NULL,
    project_id TEXT,
    generation INTEGER NOT NULL DEFAULT 1,
    session_count INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'OPEN' CHECK(state IN ('OPEN','CLOSED_NATURAL','CLOSED_LIMIT','FAILED')),
    opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TEXT,
    last_checkpoint_version INTEGER NOT NULL DEFAULT 0,
    UNIQUE(role, assignment_id, generation)
);

CREATE TABLE IF NOT EXISTS doctor_v2_web_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dialog_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT,
    end_reason TEXT CHECK(end_reason IN ('NATURAL','WATCHDOG_10M','ERROR','DIALOG_LIMIT')),
    server_wake_payload_json TEXT,
    UNIQUE(dialog_id, ordinal),
    FOREIGN KEY(dialog_id) REFERENCES doctor_v2_web_dialogs(dialog_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS doctor_v2_session_checkpoints (
    checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dialog_id TEXT NOT NULL,
    assignment_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    checkpoint_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(assignment_id, version),
    FOREIGN KEY(dialog_id) REFERENCES doctor_v2_web_dialogs(dialog_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS doctor_v2_protocol_candidates (
    protocol_id TEXT PRIMARY KEY,
    origin TEXT NOT NULL CHECK(origin IN ('INTERNAL_FIELD','EXTERNAL_WILSON','LEGACY')),
    state TEXT NOT NULL CHECK(state IN ('CANDIDATE','FIELD_TESTING','VALIDATED_1_3','VALIDATED_2_3','VALIDATED_3_3','APPROVED_ACTIVE','SUSPENDED')),
    disease_id TEXT,
    candidate_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS doctor_v2_protocol_validation_episodes (
    validation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    protocol_id TEXT NOT NULL,
    episode_key TEXT NOT NULL,
    installation_id_hash TEXT,
    patient_id TEXT,
    field_case_id TEXT,
    success INTEGER NOT NULL CHECK(success IN (0,1)),
    verified INTEGER NOT NULL CHECK(verified IN (0,1)),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(protocol_id, episode_key),
    FOREIGN KEY(protocol_id) REFERENCES doctor_v2_protocol_candidates(protocol_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS doctor_v2_validation_protocol_idx
    ON doctor_v2_protocol_validation_episodes(protocol_id, success, verified);

CREATE TABLE IF NOT EXISTS doctor_v2_protocol_publication_queue (
    publication_id INTEGER PRIMARY KEY AUTOINCREMENT,
    protocol_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'WAITING' CHECK(status IN ('WAITING','REVIEWED','PUBLISHED','REJECTED')),
    requested_by_wilson_job_id INTEGER,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(protocol_id) REFERENCES doctor_v2_protocol_candidates(protocol_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS doctor_v2_publication_status_idx
    ON doctor_v2_protocol_publication_queue(status, publication_id);

CREATE TABLE IF NOT EXISTS doctor_v2_wilson_cursors (
    stream TEXT PRIMARY KEY,
    cursor_value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS doctor_v2_wilson_jobs (
    wilson_job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL CHECK(mode IN ('HOURLY_REVIEW','NIGHTLY_RESEARCH')),
    input_cursor TEXT,
    output_cursor TEXT,
    status TEXT NOT NULL DEFAULT 'WAITING' CHECK(status IN ('WAITING','CLAIMED','DONE','FAILED')),
    batch_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS doctor_v2_wilson_jobs_status_idx
    ON doctor_v2_wilson_jobs(status, wilson_job_id);

CREATE TABLE IF NOT EXISTS doctor_v2_resolutions (
    resolution_id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    problem_key TEXT,
    domain TEXT,
    issue_id TEXT,
    terminal_resolution_required INTEGER NOT NULL DEFAULT 1 CHECK(terminal_resolution_required IN (0,1)),
    state TEXT NOT NULL DEFAULT 'OPEN' CHECK(state IN ('OPEN','DISPATCHED','WAITING_HUMAN','VERIFYING','RESOLVED')),
    current_house_job_id INTEGER,
    current_field_case_id INTEGER,
    last_event_id INTEGER,
    last_card_version INTEGER,
    human_requirement_json TEXT NOT NULL DEFAULT '{}',
    capabilities_hash TEXT,
    material_hash TEXT,
    next_recheck_at TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(patient_id, fingerprint),
    FOREIGN KEY(patient_id) REFERENCES doctor_v2_patient_cards(patient_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS doctor_v2_resolutions_state_idx
    ON doctor_v2_resolutions(state, updated_at);
