from uuid import uuid4, UUID
from typing import List, Optional
from datetime import datetime, timezone, UTC
from sqlalchemy import Date, UniqueConstraint, Index, false, func, text
from datetime import date as Date_t
from sqlalchemy import ForeignKey, Boolean, String, DateTime, Integer, Text, Enum as SQLEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from auto_apply_app.domain.value_objects import (
    ClientType,
    ContractType,
    JobBoard,
    ApplicationStatus,
    SearchStatus,
    CreditTxKind,
    MessageKind,
)


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
        "UserSubscriptionDB", back_populates="user", uselist=False,
        cascade="all, delete-orphan", passive_deletes=True,
    )
    auth_account: Mapped["AuthUserDB"] = relationship(
        "AuthUserDB", back_populates="user", uselist=False,
        cascade="all, delete-orphan", passive_deletes=True,
    )
    preferences: Mapped["UserPreferencesDB"] = relationship(
        "UserPreferencesDB", back_populates="user", uselist=False,
        cascade="all, delete-orphan", passive_deletes=True,
    )
    board_credentials: Mapped[List["BoardCredentialDB"]] = relationship(
        "BoardCredentialDB", back_populates="user",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    job_offers: Mapped[List["JobOfferDB"]] = relationship(
        "JobOfferDB", back_populates="user",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    agent_states: Mapped[List["AgentStateDB"]] = relationship(
        "AgentStateDB", back_populates="user",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    fingerprints: Mapped[List["UserFingerprintDB"]] = relationship(
        "UserFingerprintDB", back_populates="user",
        cascade="all, delete-orphan", passive_deletes=True,
    )

 
class AuthUserDB(Base):
    __tablename__ = "auth_users"
 
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # Granted out-of-band with a SQL UPDATE. Re-read on every admin request rather than
    # carried in the JWT, so revoking it is immediate instead of waiting out the token.
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        server_default=false(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Marketing consent. Only the lifecycle REMINDER honours this -- transactional
    # mail (verification, password reset) is sent regardless, as it must be.
    marketing_opt_out: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
 
    # --- Email verification (code-based) ---
    verification_code_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    verification_code_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    verification_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- Pending email change (verification-gated) ---
    # Not unique: transient, not the source of truth. Uniqueness is enforced by
    # the live email columns + the pre-check in RequestEmailChangeUseCase.
    pending_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

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
        'apec': False
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
    # Set once, when the offer transitions to SUBMITTED. application_date above
    # is find-time and cannot serve as a billing clock.
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
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
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

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
    """One device persona. A user owns a small POOL of these, not one.

    `user_id` is deliberately NOT unique any more — that constraint was what made
    a fingerprint permanent per user. Uniqueness moved to (user_id, slot), which
    is also the key the repository upserts on.
    """

    __tablename__ = "user_fingerprints"
    __table_args__ = (
        UniqueConstraint("user_id", "slot", name="uq_user_fingerprints_user_slot"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True
    )

    # --- Pool bookkeeping ---
    # slot: index within the user's pool. board: the job board this persona is
    # pinned to, so that board always sees the same machine (NULL = unassigned).
    slot: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default=text("0"))
    board: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    session_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set instead of deleting: a retired persona must stay addressable long enough
    # to clean up the cookie jar that was keyed on its id.
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Device identity (stable for the life of the persona) ---
    platform: Mapped[str] = mapped_column(String(50))
    chrome_major: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("131"))
    viewport_width: Mapped[int] = mapped_column(Integer)
    viewport_height: Mapped[int] = mapped_column(Integer)
    screen_width: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1920"))
    screen_height: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1080"))
    device_scale_factor: Mapped[float] = mapped_column()
    locale: Mapped[str] = mapped_column(String(20))
    timezone_id: Mapped[str] = mapped_column(String(50))
    hardware_concurrency: Mapped[int] = mapped_column(Integer)
    device_memory: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("8"))
    webgl_vendor: Mapped[str] = mapped_column(String(100))
    webgl_renderer: Mapped[str] = mapped_column(String(255))

    # --- Noise seeds ---
    # Persisted as the persona's baseline; the run's variant overrides them in
    # memory and is never written back.
    canvas_seed: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("''"))
    audio_seed: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("''"))

    user: Mapped["UserDB"] = relationship("UserDB", back_populates="fingerprints")


class PageViewDB(Base):
    """
    Anonymous page views — the substrate for "visitors today".

    Holds no PII by design: no IP address, no user agent, no cookie. visitor_id is an
    opaque value the browser generates for itself, and referrer is stored as a bare
    host. This table is the fastest-growing one in the schema and is pruned on a
    retention schedule.
    """

    __tablename__ = "page_views"
    __table_args__ = (
        Index("ix_page_views_visitor_created", "visitor_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    visitor_id: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    path: Mapped[str] = mapped_column(String(200))
    referrer: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )


class CreditTransactionDB(Base):
    """
    Append-only ledger of AI credit movements.

    Exists because replenish_credits() resets ai_credits_balance every billing cycle,
    which destroys the consumption history. CONSUME rows are written by
    SubscriptionRepoDB.try_consume_credits from inside its single guarded UPDATE — the
    ledger row and the decrement are one statement, so neither can exist without the
    other.
    """

    __tablename__ = "credit_transactions"
    __table_args__ = (
        Index("ix_credit_tx_user_created", "user_id", "created_at"),
    )

    # server_default matters: the CONSUME row is inserted by an INSERT ... SELECT inside
    # a CTE, which never passes through the ORM and so cannot supply a Python-side default.
    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    delta: Mapped[int] = mapped_column(Integer)          # negative = consumed
    balance_after: Mapped[int] = mapped_column(Integer)
    kind: Mapped[CreditTxKind] = mapped_column(SQLEnum(CreditTxKind, native_enum=False))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )


class MessageLogDB(Base):
    """One row per lifecycle message actually delivered to a user.

    The UNIQUE on (user_id, kind) is not bookkeeping -- it IS the guarantee that
    nobody is emailed twice. The scheduled job re-selects the same cohort every
    day it runs, and a retry, a backfill or two concurrent executions would all
    re-send without it. Insert the row in the same transaction as the send.

    Written only after the send is accepted (EmailServicePort returns bool for
    exactly this reason): a row written on a failed send would suppress the
    message permanently.
    """

    __tablename__ = "message_log"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", name="uq_message_log_user_kind"),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[MessageKind] = mapped_column(SQLEnum(MessageKind, native_enum=False))
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )


class AnnouncementDB(Base):
    """An in-app notice -- a release, a new feature -- composed from the admin
    screen and shown once per user in a modal.

    In the database rather than in the code because the whole point is publishing
    one without a deploy. Bilingual columns rather than a JSON blob: the app is
    FR/EN throughout, and a typed column is what the admin form validates against
    and what a missing translation fails loudly on.

    is_published and the starts_at/ends_at pair are separate controls on purpose.
    is_published is the author's switch -- it is how a half-written draft stays
    invisible. The window is the schedule. A draft inside its window must still
    not appear, so both are ANDed at read time.
    """

    __tablename__ = "announcements"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"),
    )

    title_fr: Mapped[str] = mapped_column(String(160))
    title_en: Mapped[str] = mapped_column(String(160))
    body_fr: Mapped[str] = mapped_column(Text)
    body_en: Mapped[str] = mapped_column(Text)

    # A notice does not have to lead anywhere. When cta_url is null the modal
    # renders a dismiss button alone, so the labels are optional with it.
    cta_label_fr: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    cta_label_en: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    cta_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # An emoji, stored as text. Not an enum: the set of things worth illustrating
    # is not knowable in advance, and this is decoration, not behaviour.
    icon: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    is_published: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    # Null means open-ended. The alternative -- a far-future sentinel -- reads as a
    # real date in the admin list and would eventually arrive.
    ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class AnnouncementViewDB(Base):
    """One row per (user, announcement) the user has dismissed.

    Server-side rather than localStorage because a dismissal has to follow the
    user across devices -- being shown the same release note again on your phone
    reads as a bug. localStorage was only ever acceptable for anonymous visitors,
    which is why VisitorStorage still uses it and this does not.

    The UNIQUE is what makes a double-click, or a dismiss racing a reload, a
    no-op rather than a duplicate.
    """

    __tablename__ = "announcement_views"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "announcement_id", name="uq_announcement_view_user_announcement"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    announcement_id: Mapped[UUID] = mapped_column(
        ForeignKey("announcements.id", ondelete="CASCADE"), index=True
    )
    dismissed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

