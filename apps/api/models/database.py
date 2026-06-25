import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    login: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    installations: Mapped[list["Installation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Installation(Base):
    __tablename__ = "installations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    installation_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(String(50), nullable=False)  # User | Organization
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="installations")
    repositories: Mapped[list["Repository"]] = relationship(
        back_populates="installation", cascade="all, delete-orphan"
    )


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    github_repo_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    installation: Mapped["Installation"] = relationship(back_populates="repositories")
    workflow_runs: Mapped[list["WorkflowRun"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    github_run_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    workflow_name: Mapped[str | None] = mapped_column(String(255))
    head_sha: Mapped[str | None] = mapped_column(String(40))
    pr_number: Mapped[int | None] = mapped_column(Integer)
    conclusion: Mapped[str] = mapped_column(String(50), default="failure")
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analysis_ms: Mapped[int | None] = mapped_column(Integer)
    comment_posted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_detail: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index(
            "idx_runs_repo_triggered",
            "repository_id",
            "triggered_at",
            postgresql_where="status = 'completed'",
        ),
    )

    repository: Mapped["Repository"] = relationship(back_populates="workflow_runs")
    failure_analysis: Mapped["FailureAnalysis | None"] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class FailureAnalysis(Base):
    __tablename__ = "failure_analyses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    error_category: Mapped[str] = mapped_column(String(100), default="unknown")
    failed_step: Mapped[str | None] = mapped_column(Text)
    cascading_steps: Mapped[str | None] = mapped_column(Text)  # JSON array as text
    root_cause: Mapped[str | None] = mapped_column(Text)
    fix_suggestion: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[int | None] = mapped_column(Integer)  # 0-100
    source: Mapped[str] = mapped_column(String(50), default="unknown")  # rule_engine | gemini | ollama
    rule_id: Mapped[str | None] = mapped_column(String(255))
    redaction_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    run: Mapped["WorkflowRun"] = relationship(back_populates="failure_analysis")


class ErrorPattern(Base):
    __tablename__ = "error_patterns"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pattern_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(50), nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    fix: Mapped[str] = mapped_column(Text, nullable=False)
    fix_url: Mapped[str | None] = mapped_column(Text)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    success_rate: Mapped[int | None] = mapped_column(Integer)  # 0-100, null until 5+ hits
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (UniqueConstraint("pattern_id", name="uq_error_patterns_pattern_id"),)