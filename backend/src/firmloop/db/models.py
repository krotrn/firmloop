"""SQLAlchemy table definitions. IDs are ULID strings and timestamps ISO-8601 UTC text, both
generated in Python; JSON columns are TEXT holding Pydantic-serialised JSON."""

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    desc,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

RUN_STATUSES = (
    "pending",
    "understanding",
    "awaiting_answer",
    "awaiting_confirmation",
    "generating_tests",
    "coding",
    "checking",
    "native_testing",
    "building",
    "flashing",
    "verifying",
    "succeeded",
    "failed",
    "cancelled",
)
TERMINAL_RUN_STATUSES = ("succeeded", "failed", "cancelled")
WAITING_RUN_STATUSES = ("awaiting_answer", "awaiting_confirmation")
WORKING_RUN_STATUSES = tuple(
    s for s in RUN_STATUSES if s not in TERMINAL_RUN_STATUSES and s not in WAITING_RUN_STATUSES
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_archived_updated", "archived", desc("updated_at")),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, server_default=text("'Untitled session'"))
    current_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("versions.id", ondelete="SET NULL"), nullable=True
    )
    archived: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class Version(Base):
    __tablename__ = "versions"
    __table_args__ = (
        UniqueConstraint("session_id", "number", name="uq_versions_session_id_number"),
        CheckConstraint(_in("status", ("candidate", "promoted", "rejected")), name="status"),
        Index("ix_versions_session_number", "session_id", "number"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, server_default=text("'candidate'"))
    parent_version_id: Mapped[str | None] = mapped_column(ForeignKey("versions.id"), nullable=True)
    created_from_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    spec_json: Mapped[str] = mapped_column(Text)
    tests_json: Mapped[str] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text)
    lib_version: Mapped[str] = mapped_column(Text)
    firmware_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    firmware_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    firmware_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_kind: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)
    promoted_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(_in("status", RUN_STATUSES), name="status"),
        CheckConstraint(
            f"({_in('status', TERMINAL_RUN_STATUSES)}) = (open_slot IS NULL)", name="open_slot"
        ),
        Index("ix_runs_session_started", "session_id", desc("started_at")),
        Index("ix_runs_status", "status"),
        Index("ux_runs_open_slot", "open_slot", unique=True),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text, server_default=text("'request'"))
    retried_from_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    open_slot: Mapped[int | None] = mapped_column(Integer, nullable=True, server_default=text("1"))
    request_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_mode: Mapped[str] = mapped_column(Text, server_default=text("'text'"))
    base_version_id: Mapped[str | None] = mapped_column(ForeignKey("versions.id"), nullable=True)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    question_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    answers_json: Mapped[str] = mapped_column(Text, server_default=text("'[]'"))
    reject_feedback_json: Mapped[str] = mapped_column(Text, server_default=text("'[]'"))
    proposed_spec_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec_diff_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_spec_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_approved: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    tests_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_attempts: Mapped[int] = mapped_column(Integer, server_default=text("5"))
    best_attempt_id: Mapped[str | None] = mapped_column(ForeignKey("attempts.id"), nullable=True)
    result_version_id: Mapped[str | None] = mapped_column(ForeignKey("versions.id"), nullable=True)
    target_version_id: Mapped[str | None] = mapped_column(ForeignKey("versions.id"), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)
    finished_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (UniqueConstraint("run_id", "number", name="uq_attempts_run_id_number"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text, server_default=text("'agent'"))
    code: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage_reached: Mapped[str] = mapped_column(Text)
    safety_ok: Mapped[int | None] = mapped_column(Integer, nullable=True)
    safety_violations_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    compile_ok: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compile_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    tests_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tests_passed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    test_results_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    feedback_sent: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(Text)


class BenchmarkRun(Base):
    __tablename__ = "benchmark_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    max_attempts: Mapped[int] = mapped_column(Integer)
    task_ids_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'running'"))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str] = mapped_column(Text)
    finished_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class BenchmarkResult(Base):
    __tablename__ = "benchmark_results"
    __table_args__ = (
        UniqueConstraint("benchmark_run_id", "task_id", name="uq_benchmark_results_run_task"),
        Index("ix_bench_results_run", "benchmark_run_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    benchmark_run_id: Mapped[str] = mapped_column(
        ForeignKey("benchmark_runs.id", ondelete="CASCADE")
    )
    task_id: Mapped[str] = mapped_column(Text)
    tier: Mapped[int] = mapped_column(Integer)
    understanding_pass: Mapped[int] = mapped_column(Integer)
    understanding_sound: Mapped[int] = mapped_column(Integer)
    understanding_complete: Mapped[int] = mapped_column(Integer)
    mutants_rejected: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    mutants_total: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    implementation_pass: Mapped[int] = mapped_column(Integer)
    attempts_used: Mapped[int] = mapped_column(Integer)
    schema_retries: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    compile_failures: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    safety_failures: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    input_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    duration_ms: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[str] = mapped_column(Text, server_default=text("'{}'"))
    created_at: Mapped[str] = mapped_column(Text)


class LlmCall(Base):
    __tablename__ = "llm_calls"
    __table_args__ = (
        Index("ix_llm_calls_run", "run_id"),
        Index("ix_llm_calls_bench", "benchmark_result_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"))
    benchmark_result_id: Mapped[str | None] = mapped_column(
        ForeignKey("benchmark_results.id", ondelete="CASCADE")
    )
    purpose: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    schema_retry: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    ok: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_json: Mapped[str] = mapped_column(Text)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text)


class DeviceCheck(Base):
    __tablename__ = "device_checks"
    __table_args__ = (Index("ix_device_checks_run", "run_id"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    version_id: Mapped[str] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    details_json: Mapped[str] = mapped_column(Text, server_default=text("'{}'"))
    created_at: Mapped[str] = mapped_column(Text)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class DeviceState(Base):
    __tablename__ = "device_state"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, server_default=text("1"))
    expected_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("versions.id", ondelete="SET NULL"), nullable=True
    )
    pending_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("versions.id", ondelete="SET NULL"), nullable=True
    )
    mode: Mapped[str] = mapped_column(Text, server_default=text("'virtual'"))
    updated_at: Mapped[str] = mapped_column(Text)
