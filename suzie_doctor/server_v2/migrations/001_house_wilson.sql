-- Suzie Doctor Server v2 migration
-- Draft schema extension only. Apply to a BACKUP COPY first.
-- Does not drop or rename existing tables.

PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS doctor_patients (
  patient_id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL UNIQUE,
  label TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS doctor_patient_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  patient_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  source TEXT NOT NULL,
  significance TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(patient_id) REFERENCES doctor_patients(patient_id)
);
CREATE INDEX IF NOT EXISTS idx_doctor_patient_events_patient
  ON doctor_patient_events(patient_id, event_id);

CREATE TABLE IF NOT EXISTS doctor_patient_cards (
  patient_id TEXT PRIMARY KEY,
  version INTEGER NOT NULL DEFAULT 1,
  state_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(patient_id) REFERENCES doctor_patients(patient_id)
);

CREATE TABLE IF NOT EXISTS doctor_house_jobs (
  job_id INTEGER PRIMARY KEY AUTOINCREMENT,
  patient_id TEXT NOT NULL,
  card_version INTEGER NOT NULL,
  trigger_event_id INTEGER,
  status TEXT NOT NULL DEFAULT 'WAITING',
  priority TEXT NOT NULL DEFAULT 'NORMAL',
  decision TEXT,
  decision_json TEXT,
  created_at TEXT NOT NULL,
  claimed_at TEXT,
  completed_at TEXT,
  FOREIGN KEY(patient_id) REFERENCES doctor_patients(patient_id)
);
CREATE INDEX IF NOT EXISTS idx_doctor_house_jobs_status
  ON doctor_house_jobs(status, priority, job_id);

CREATE TABLE IF NOT EXISTS doctor_field_queue (
  queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id INTEGER NOT NULL UNIQUE,
  patient_id TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT 'NORMAL',
  status TEXT NOT NULL DEFAULT 'WAITING',
  house_job_id INTEGER,
  created_at TEXT NOT NULL,
  assigned_at TEXT,
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_doctor_field_queue_status
  ON doctor_field_queue(status, priority, queue_id);

CREATE TABLE IF NOT EXISTS doctor_role_slots (
  role_type TEXT NOT NULL,
  slot_no INTEGER NOT NULL,
  assignment_ref TEXT,
  status TEXT NOT NULL DEFAULT 'FREE',
  lease_expires_at TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(role_type, slot_no)
);

INSERT OR IGNORE INTO doctor_role_slots(role_type,slot_no,status,updated_at) VALUES
 ('FIELD_SUZIE',1,'FREE',CURRENT_TIMESTAMP),
 ('FIELD_SUZIE',2,'FREE',CURRENT_TIMESTAMP),
 ('FIELD_SUZIE',3,'FREE',CURRENT_TIMESTAMP),
 ('FIELD_SUZIE',4,'FREE',CURRENT_TIMESTAMP),
 ('HOUSE',1,'FREE',CURRENT_TIMESTAMP),
 ('WILSON',1,'FREE',CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS doctor_web_dialogs (
  dialog_id TEXT PRIMARY KEY,
  role_type TEXT NOT NULL,
  logical_job_type TEXT NOT NULL,
  logical_job_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  generation INTEGER NOT NULL DEFAULT 1,
  session_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'OPEN',
  opened_at TEXT NOT NULL,
  closed_at TEXT,
  close_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_doctor_web_dialogs_job
  ON doctor_web_dialogs(logical_job_type, logical_job_id, status);

CREATE TABLE IF NOT EXISTS doctor_web_sessions (
  session_id TEXT PRIMARY KEY,
  dialog_id TEXT NOT NULL,
  session_no INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  watchdog_at TEXT NOT NULL,
  ended_at TEXT,
  end_reason TEXT,
  continuation_requested INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(dialog_id) REFERENCES doctor_web_dialogs(dialog_id),
  UNIQUE(dialog_id, session_no)
);

CREATE TABLE IF NOT EXISTS doctor_session_checkpoints (
  checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
  dialog_id TEXT NOT NULL,
  logical_job_type TEXT NOT NULL,
  logical_job_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  checkpoint_version INTEGER NOT NULL,
  state_json TEXT NOT NULL,
  state_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(logical_job_type, logical_job_id, generation, checkpoint_version)
);

CREATE TABLE IF NOT EXISTS doctor_house_decisions (
  decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  patient_id TEXT NOT NULL,
  card_version INTEGER NOT NULL,
  decision TEXT NOT NULL,
  priority TEXT,
  assessment_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(job_id) REFERENCES doctor_house_jobs(job_id)
);

CREATE TABLE IF NOT EXISTS doctor_wilson_jobs (
  job_id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT NOT NULL,
  input_cursor TEXT,
  output_cursor TEXT,
  status TEXT NOT NULL DEFAULT 'WAITING',
  batch_json TEXT NOT NULL,
  result_json TEXT,
  created_at TEXT NOT NULL,
  claimed_at TEXT,
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_doctor_wilson_jobs_status
  ON doctor_wilson_jobs(status, mode, job_id);

CREATE TABLE IF NOT EXISTS doctor_protocol_validation_episodes (
  episode_id TEXT PRIMARY KEY,
  protocol_id TEXT NOT NULL,
  disease_id TEXT,
  client_id TEXT NOT NULL,
  case_id INTEGER,
  independent_group TEXT NOT NULL,
  outcome TEXT NOT NULL,
  verify_json TEXT NOT NULL,
  counted_confirmation INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doctor_protocol_validation_protocol
  ON doctor_protocol_validation_episodes(protocol_id, counted_confirmation);

CREATE TABLE IF NOT EXISTS doctor_knowledge_evidence (
  evidence_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  origin TEXT NOT NULL,
  disease_id TEXT,
  protocol_id TEXT,
  case_id INTEGER,
  source_ref TEXT,
  evidence_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
