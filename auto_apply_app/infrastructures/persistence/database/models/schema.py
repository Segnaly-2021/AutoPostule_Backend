from uuid import uuid4, UUID
from typing import List, Optional
from datetime import datetime, timezone, UTC
from sqlalchemy import Date, UniqueConstraint
from datetime import date as Date_t
from sqlalchemy import ForeignKey, Boolean, String, DateTime, Integer, Text, Enum as SQLEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from auto_apply_app.domain.value_objects import ClientType, ContractType, JobBoard, ApplicationStatus, SearchStatus


class Base(DeclarativeBase):
    pass


class UserDB(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    firstname: Mapped[str] = mapped_column(String(100))
    lastname: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # New field
    resume_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    resume_file_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    current_position: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    current_company: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    school_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    graduation_year: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    major: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    study_level: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
   

    # Relationships
    subscription: Mapped["UserSubscriptionDB"] = relationship(
        "UserSubscriptionDB", back_populates="user", uselist=False
    )
    auth_account: Mapped["AuthUserDB"] = relationship(
        "AuthUserDB", back_populates="user", uselist=False
    )
    preferences: Mapped["UserPreferencesDB"] = relationship(
        "UserPreferencesDB", back_populates="user", uselist=False
    )
    board_credentials: Mapped[List["BoardCredentialDB"]] = relationship(
        "BoardCredentialDB", back_populates="user"
    )
    job_offers: Mapped[List["JobOfferDB"]] = relationship(
        "JobOfferDB", back_populates="user", cascade="all, delete-orphan"
    )
    agent_states: Mapped[List["AgentStateDB"]] = relationship(
        "AgentStateDB",
        back_populates="user",
        cascade="all, delete-orphan",
    )

     # 🚨 ADD THIS:
    fingerprint: Mapped[Optional["UserFingerprintDB"]] = relationship(
        "UserFingerprintDB", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class AuthUserDB(Base):
    __tablename__ = "auth_users"

    
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True, 
        index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["UserDB"] = relationship("UserDB", back_populates="auth_account")


class UserSubscriptionDB(Base):
    __tablename__ = "user_subscriptions"

    # 🚨 FIX: id is PK, user_id is unique
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[ClientType] = mapped_column(
        SQLEnum(ClientType),
        default=ClientType.FREE
    )
    ai_credits_balance: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(default=False)
    is_past_due: Mapped[bool] = mapped_column(default=False)
    grace_days: Mapped[int] = mapped_column(Integer, default=0)
    current_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC)
    )
    current_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC)
    )
    cancel_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_billing_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)

    user: Mapped["UserDB"] = relationship("UserDB", back_populates="subscription")


class UserPreferencesDB(Base):
    __tablename__ = "user_preferences"

    
    # 🚨 FIX: id is PK, user_id is unique
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True
    )
    is_full_automation: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_model: Mapped[str] = mapped_column(String(50), default="gemini")
    # Store active_boards dict as JSONB: {"hellowork": true, "wttj": false, ...}
    active_boards: Mapped[dict] = mapped_column(JSONB, default=lambda: {
        'hellowork': True,
        'wttj': False,
        'apec': False,
        'jobteaser': False
    })
    creativity_level: Mapped[int] = mapped_column(Integer, default=8)

    user: Mapped["UserDB"] = relationship("UserDB", back_populates="preferences")


class BoardCredentialDB(Base):
    __tablename__ = "board_credentials"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True
    )
    job_board: Mapped[str] = mapped_column(String(50), index=True)
    login_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    password_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["UserDB"] = relationship("UserDB", back_populates="board_credentials")


class JobSearchDB(Base):
    __tablename__ = "job_searches"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    
    # 🚨 FIX: Added ondelete="CASCADE"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), 
        index=True
    )
    
    job_title: Mapped[str] = mapped_column(String(200))
    job_boards: Mapped[List[JobBoard]] = mapped_column(
        ARRAY(SQLEnum(JobBoard, native_enum=False)), 
        default=list
    )
    search_status: Mapped[SearchStatus] = mapped_column(
        SQLEnum(SearchStatus, native_enum=False),
        default=SearchStatus.PENDING
    )
    contract_types: Mapped[Optional[List[ContractType]]] = mapped_column(
        ARRAY(SQLEnum(ContractType, native_enum=False)),
        nullable=True
    )
    min_salary: Mapped[int] = mapped_column(Integer, default=0)
    location: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    job_offers: Mapped[List["JobOfferDB"]] = relationship(
        "JobOfferDB", back_populates="search", cascade="all, delete-orphan"
    )


class JobOfferDB(Base):
    __tablename__ = "job_offers"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    search_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_searches.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    
    url: Mapped[str] = mapped_column(String(500), index=True)
    form_url: Mapped[str] = mapped_column(String(500))
    company_name: Mapped[str] = mapped_column(String(200), index=True)
    job_title: Mapped[str] = mapped_column(String(200))
    clean_title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # New field for cleaned job title
    location: Mapped[str] = mapped_column(String(200))
    job_board: Mapped[JobBoard] = mapped_column(SQLEnum(JobBoard, native_enum=False))
    job_posting_id: Mapped[Optional[str]] = mapped_column(String(100), index=True, nullable=True)
    cover_letter: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ranking: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    job_desc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    application_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )
    followup_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[ApplicationStatus] = mapped_column(
        SQLEnum(ApplicationStatus, native_enum=False),
        default=ApplicationStatus.FOUND
    )
    has_interview: Mapped[bool] = mapped_column(Boolean, default=False)
    has_response: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Optional but highly recommended: Add the back-reference relationship to UserDB
    # In UserDB add: job_offers: Mapped[List["JobOfferDB"]] = relationship("JobOfferDB", back_populates="user")
    user: Mapped["UserDB"] = relationship("UserDB", back_populates="job_offers") # In JobOfferDB

    search: Mapped["JobSearchDB"] = relationship("JobSearchDB", back_populates="job_offers")

class AgentStateDB(Base):
    __tablename__ = "agent_states"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    search_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_searches.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    is_shutdown: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["UserDB"] = relationship("UserDB", back_populates="agent_states")
    

class AgentUsageDB(Base):
    __tablename__ = "agent_usages"
    __table_args__ = (
        UniqueConstraint("user_id", "usage_date", name="uq_agent_usage_user_date"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    usage_date: Mapped[Date_t] = mapped_column(Date, index=True)
    runs_count: Mapped[int] = mapped_column(Integer, default=0)
    last_completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class FreeSearchUsageDB(Base):
    __tablename__ = "free_search_usages"
    __table_args__ = (
        UniqueConstraint("user_id", "usage_date", name="uq_free_search_usage_user_date"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    usage_date: Mapped[Date_t] = mapped_column(Date, index=True)
    searches_count: Mapped[int] = mapped_column(Integer, default=0)

class UserFingerprintDB(Base):
    __tablename__ = "user_fingerprints"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True
    )
    user_agent: Mapped[str] = mapped_column(String(500))
    viewport_width: Mapped[int] = mapped_column(Integer)
    viewport_height: Mapped[int] = mapped_column(Integer)
    device_scale_factor: Mapped[float] = mapped_column()
    locale: Mapped[str] = mapped_column(String(20))
    timezone_id: Mapped[str] = mapped_column(String(50))
    hardware_concurrency: Mapped[int] = mapped_column(Integer)
    platform: Mapped[str] = mapped_column(String(50))
    webgl_vendor: Mapped[str] = mapped_column(String(100))
    webgl_renderer: Mapped[str] = mapped_column(String(255))

    user: Mapped["UserDB"] = relationship("UserDB", back_populates="fingerprint")


