"""Initial schema: all tables, constraints, indexes and the seeded device_state row.

Revision ID: 0001
Revises:
Create Date: 2026-10-30
"""

# ruff: noqa: E501
from collections.abc import Sequence
from datetime import UTC, datetime

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen DDL: the migration must not change when models.py does.
STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE sessions (
    id TEXT NOT NULL,
    title TEXT DEFAULT 'Untitled session' NOT NULL,
    current_version_id TEXT,
    archived INTEGER DEFAULT 0 NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CONSTRAINT pk_sessions PRIMARY KEY (id),
    CONSTRAINT fk_sessions_current_version_id_versions FOREIGN KEY(current_version_id) REFERENCES versions (id) ON DELETE SET NULL
)""",
    """CREATE INDEX ix_sessions_archived_updated ON sessions (archived, updated_at DESC)""",
    """CREATE TABLE versions (
    id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    number INTEGER,
    status TEXT DEFAULT 'candidate' NOT NULL,
    parent_version_id TEXT,
    created_from_run_id TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    tests_json TEXT NOT NULL,
    code TEXT NOT NULL,
    lib_version TEXT NOT NULL,
    firmware_dir TEXT,
    firmware_sha256 TEXT,
    firmware_size INTEGER,
    target_kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    promoted_at TEXT,
    CONSTRAINT pk_versions PRIMARY KEY (id),
    CONSTRAINT uq_versions_session_id_number UNIQUE (session_id, number),
    CONSTRAINT ck_versions_status CHECK (status IN ('candidate', 'promoted', 'rejected')),
    CONSTRAINT fk_versions_session_id_sessions FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE,
    CONSTRAINT fk_versions_parent_version_id_versions FOREIGN KEY(parent_version_id) REFERENCES versions (id),
    CONSTRAINT fk_versions_created_from_run_id_runs FOREIGN KEY(created_from_run_id) REFERENCES runs (id)
)""",
    """CREATE INDEX ix_versions_session_number ON versions (session_id, number)""",
    """CREATE TABLE runs (
    id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    kind TEXT DEFAULT 'request' NOT NULL,
    retried_from_run_id TEXT,
    open_slot INTEGER DEFAULT 1,
    request_text TEXT,
    input_mode TEXT DEFAULT 'text' NOT NULL,
    base_version_id TEXT,
    status TEXT DEFAULT 'pending' NOT NULL,
    question_json TEXT,
    answers_json TEXT DEFAULT '[]' NOT NULL,
    reject_feedback_json TEXT DEFAULT '[]' NOT NULL,
    proposed_spec_json TEXT,
    spec_diff_json TEXT,
    confirmed_spec_json TEXT,
    auto_approved INTEGER DEFAULT 0 NOT NULL,
    tests_json TEXT,
    provider TEXT,
    model TEXT,
    max_attempts INTEGER DEFAULT 5 NOT NULL,
    best_attempt_id TEXT,
    result_version_id TEXT,
    target_version_id TEXT,
    failure_code TEXT,
    failure_message TEXT,
    failure_details_json TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    CONSTRAINT pk_runs PRIMARY KEY (id),
    CONSTRAINT ck_runs_status CHECK (status IN ('pending', 'understanding', 'awaiting_answer', 'awaiting_confirmation', 'generating_tests', 'coding', 'checking', 'native_testing', 'building', 'flashing', 'verifying', 'succeeded', 'failed', 'cancelled')),
    CONSTRAINT ck_runs_open_slot CHECK ((status IN ('succeeded', 'failed', 'cancelled')) = (open_slot IS NULL)),
    CONSTRAINT fk_runs_session_id_sessions FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE,
    CONSTRAINT fk_runs_retried_from_run_id_runs FOREIGN KEY(retried_from_run_id) REFERENCES runs (id),
    CONSTRAINT fk_runs_base_version_id_versions FOREIGN KEY(base_version_id) REFERENCES versions (id),
    CONSTRAINT fk_runs_best_attempt_id_attempts FOREIGN KEY(best_attempt_id) REFERENCES attempts (id),
    CONSTRAINT fk_runs_result_version_id_versions FOREIGN KEY(result_version_id) REFERENCES versions (id),
    CONSTRAINT fk_runs_target_version_id_versions FOREIGN KEY(target_version_id) REFERENCES versions (id)
)""",
    """CREATE INDEX ix_runs_session_started ON runs (session_id, started_at DESC)""",
    """CREATE INDEX ix_runs_status ON runs (status)""",
    """CREATE UNIQUE INDEX ux_runs_open_slot ON runs (open_slot)""",
    """CREATE TABLE attempts (
    id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    number INTEGER NOT NULL,
    source TEXT DEFAULT 'agent' NOT NULL,
    code TEXT,
    stage_reached TEXT NOT NULL,
    safety_ok INTEGER,
    safety_violations_json TEXT,
    compile_ok INTEGER,
    compile_log TEXT,
    tests_total INTEGER,
    tests_passed INTEGER,
    test_results_json TEXT,
    feedback_sent TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL,
    CONSTRAINT pk_attempts PRIMARY KEY (id),
    CONSTRAINT uq_attempts_run_id_number UNIQUE (run_id, number),
    CONSTRAINT fk_attempts_run_id_runs FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE CASCADE
)""",
    """CREATE TABLE benchmark_runs (
    id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    max_attempts INTEGER NOT NULL,
    task_ids_json TEXT NOT NULL,
    status TEXT DEFAULT 'running' NOT NULL,
    notes TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    CONSTRAINT pk_benchmark_runs PRIMARY KEY (id)
)""",
    """CREATE TABLE benchmark_results (
    id TEXT NOT NULL,
    benchmark_run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    tier INTEGER NOT NULL,
    understanding_pass INTEGER NOT NULL,
    understanding_sound INTEGER NOT NULL,
    understanding_complete INTEGER NOT NULL,
    mutants_rejected INTEGER DEFAULT 0 NOT NULL,
    mutants_total INTEGER DEFAULT 0 NOT NULL,
    implementation_pass INTEGER NOT NULL,
    attempts_used INTEGER NOT NULL,
    schema_retries INTEGER DEFAULT 0 NOT NULL,
    compile_failures INTEGER DEFAULT 0 NOT NULL,
    safety_failures INTEGER DEFAULT 0 NOT NULL,
    input_tokens INTEGER DEFAULT 0 NOT NULL,
    output_tokens INTEGER DEFAULT 0 NOT NULL,
    duration_ms INTEGER NOT NULL,
    error_code TEXT,
    spec_json TEXT,
    final_code TEXT,
    details_json TEXT DEFAULT '{}' NOT NULL,
    created_at TEXT NOT NULL,
    CONSTRAINT pk_benchmark_results PRIMARY KEY (id),
    CONSTRAINT uq_benchmark_results_run_task UNIQUE (benchmark_run_id, task_id),
    CONSTRAINT fk_benchmark_results_benchmark_run_id_benchmark_runs FOREIGN KEY(benchmark_run_id) REFERENCES benchmark_runs (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_bench_results_run ON benchmark_results (benchmark_run_id)""",
    """CREATE TABLE llm_calls (
    id TEXT NOT NULL,
    run_id TEXT,
    attempt_id TEXT,
    benchmark_result_id TEXT,
    purpose TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    schema_retry INTEGER DEFAULT 0 NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER NOT NULL,
    ok INTEGER NOT NULL,
    error_code TEXT,
    error_message TEXT,
    request_json TEXT NOT NULL,
    response_text TEXT,
    created_at TEXT NOT NULL,
    CONSTRAINT pk_llm_calls PRIMARY KEY (id),
    CONSTRAINT fk_llm_calls_run_id_runs FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE CASCADE,
    CONSTRAINT fk_llm_calls_attempt_id_attempts FOREIGN KEY(attempt_id) REFERENCES attempts (id) ON DELETE CASCADE,
    CONSTRAINT fk_llm_calls_benchmark_result_id_benchmark_results FOREIGN KEY(benchmark_result_id) REFERENCES benchmark_results (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_llm_calls_bench ON llm_calls (benchmark_result_id)""",
    """CREATE INDEX ix_llm_calls_run ON llm_calls (run_id)""",
    """CREATE TABLE device_checks (
    id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    details_json TEXT DEFAULT '{}' NOT NULL,
    created_at TEXT NOT NULL,
    CONSTRAINT pk_device_checks PRIMARY KEY (id),
    CONSTRAINT fk_device_checks_run_id_runs FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE CASCADE,
    CONSTRAINT fk_device_checks_version_id_versions FOREIGN KEY(version_id) REFERENCES versions (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_device_checks_run ON device_checks (run_id)""",
    """CREATE TABLE settings (
    "key" TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CONSTRAINT pk_settings PRIMARY KEY ("key")
)""",
    """CREATE TABLE device_state (
    id INTEGER DEFAULT 1 NOT NULL,
    expected_version_id TEXT,
    pending_version_id TEXT,
    mode TEXT DEFAULT 'virtual' NOT NULL,
    updated_at TEXT NOT NULL,
    CONSTRAINT pk_device_state PRIMARY KEY (id),
    CONSTRAINT ck_device_state_single_row CHECK (id = 1),
    CONSTRAINT fk_device_state_expected_version_id_versions FOREIGN KEY(expected_version_id) REFERENCES versions (id) ON DELETE SET NULL,
    CONSTRAINT fk_device_state_pending_version_id_versions FOREIGN KEY(pending_version_id) REFERENCES versions (id) ON DELETE SET NULL
)""",
)

TABLES_DROP_ORDER: tuple[str, ...] = (
    "device_state",
    "settings",
    "device_checks",
    "llm_calls",
    "benchmark_results",
    "benchmark_runs",
    "attempts",
    "runs",
    "versions",
    "sessions",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
    now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    op.execute(f"INSERT INTO device_state (id, mode, updated_at) VALUES (1, 'virtual', '{now}')")


def downgrade() -> None:
    # Foreign keys are circular (runs <-> versions), so drop with enforcement off.
    op.execute("PRAGMA foreign_keys=OFF")
    for table in TABLES_DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("PRAGMA foreign_keys=ON")
