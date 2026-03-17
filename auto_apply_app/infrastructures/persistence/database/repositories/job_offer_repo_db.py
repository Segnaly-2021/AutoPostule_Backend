# =============================================================================
# job_offer_repo_db.py
# =============================================================================
from uuid import UUID
from datetime import datetime, timedelta, UTC, timezone
from typing import Set, List, Tuple, Dict
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.application.repositories.job_offer_repo import JobOfferRepository
from auto_apply_app.infrastructures.persistence.database.models.schema import JobOfferDB
from auto_apply_app.domain.exceptions import JobNotFoundError
from auto_apply_app.domain.value_objects import ApplicationStatus


class JobOfferRepoDB(JobOfferRepository):
    IMMUTABLE_FIELDS = frozenset({"id", "search_id", "job_board", "application_date"})

    def __init__(self, session: AsyncSession):
        self.session = session

    # =========================================================================
    # CORE CRUD
    # =========================================================================

    async def save(self, offer: JobOffer) -> None:
        """Upsert — handles both create and status updates."""
        try:
            posting_id = offer.get_job_posting_id()
        except Exception:
            posting_id = None

        offer_db = JobOfferDB(
            url=offer.url,
            form_url=offer.form_url,
            search_id=offer.search_id,
            user_id=offer.user_id,  # ✅ FIXED: Added missing user_id
            company_name=offer.company_name,
            job_title=offer.job_title,
            location=offer.location,
            job_board=offer.job_board,
            job_posting_id=posting_id,
            cover_letter=offer.cover_letter,
            ranking=offer.ranking,
            job_desc=offer.job_desc,
            application_date=offer.application_date or datetime.now(UTC),
            followup_date=offer.followup_date,
            status=offer.status,
            has_interview=offer.has_interview,
            has_response=offer.has_response,
        )
        await self.session.merge(offer_db)

    async def save_all(self, offers: List[JobOffer]) -> None:
        """Batch upsert — used by agent after LLM processing."""
        for offer in offers:
            await self.save(offer)

    async def get(self, offer_id: UUID) -> JobOffer:
        result = await self.session.execute(
            select(JobOfferDB).where(JobOfferDB.id == offer_id)
        )
        offer_db = result.scalar_one_or_none()
        if offer_db is None:
            raise JobNotFoundError(f"Job offer {offer_id} not found")
        return self._map_to_entity(offer_db)

    async def get_by_search(self, search_id: UUID) -> List[JobOffer]:
        result = await self.session.execute(
            select(JobOfferDB).where(JobOfferDB.search_id == search_id)
        )
        return [self._map_to_entity(o) for o in result.scalars().all()]

    async def get_by_search_and_status(
        self,
        search_id: UUID,
        status: ApplicationStatus
    ) -> List[JobOffer]:
        result = await self.session.execute(
            select(JobOfferDB)
            .where(JobOfferDB.search_id == search_id)
            .where(JobOfferDB.status == status)
        )
        return [self._map_to_entity(o) for o in result.scalars().all()]

    async def delete(self, offer_id: UUID) -> None:
        result = await self.session.execute(
            select(JobOfferDB).where(JobOfferDB.id == offer_id)
        )
        offer_db = result.scalar_one_or_none()
        if offer_db:
            await self.session.delete(offer_db)

    async def get_total_job(self) -> int:
        result = await self.session.execute(select(func.count(JobOfferDB.id)))
        return result.scalar_one()

    # =========================================================================
    # DEDUPLICATION
    # =========================================================================

    async def get_recent_application_hashes(self, user_id: UUID, days: int = 14) -> Set[str]:
        since_date = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(JobOfferDB.job_posting_id)
            # ✅ OPTIMIZED: Removed join(JobSearchDB), querying user_id directly
            .where(JobOfferDB.user_id == user_id) 
            .where(JobOfferDB.application_date >= since_date)
            .where(JobOfferDB.job_posting_id.isnot(None))
        )
        result = await self.session.execute(stmt)
        return set(result.scalars().all())

    # =========================================================================
    # STATUS UPDATES (used by tracker UI)
    # =========================================================================

    async def update_response_status(
        self,
        job_id: str,
        has_response: bool,
        status: ApplicationStatus = ApplicationStatus.SUBMITTED
    ) -> JobOffer:
        try:
            uuid_id = UUID(job_id)
        except ValueError:
            raise JobNotFoundError(f"Invalid UUID: {job_id}")

        result = await self.session.execute(
            select(JobOfferDB)
            .where(JobOfferDB.id == uuid_id)
            .where(JobOfferDB.status == status)
        )
        offer_db = result.scalar_one_or_none()
        if offer_db is None:
            raise JobNotFoundError(f"Job {job_id} not found with status {status.name}")

        offer_db.has_response = has_response
        return self._map_to_entity(offer_db)

    async def update_interview_status(
        self,
        job_id: str,
        has_interview: bool,
        status: ApplicationStatus = ApplicationStatus.SUBMITTED
    ) -> JobOffer:
        try:
            uuid_id = UUID(job_id)
        except ValueError:
            raise JobNotFoundError(f"Invalid UUID: {job_id}")

        result = await self.session.execute(
            select(JobOfferDB)
            .where(JobOfferDB.id == uuid_id)
            .where(JobOfferDB.status == status)
        )
        offer_db = result.scalar_one_or_none()
        if offer_db is None:
            raise JobNotFoundError(f"Job {job_id} not found with status {status.name}")

        offer_db.has_interview = has_interview
        return self._map_to_entity(offer_db)

    # =========================================================================
    # DASHBOARD: TRACKER (paginated, filtered list)
    # =========================================================================

    async def get_user_applications(
        self,
        user_id: str,
        filters: dict,
        pagination: dict,
        status: ApplicationStatus = ApplicationStatus.SUBMITTED
    ) -> Tuple[List[JobOffer], int, Dict]:
        """
        Paginated, filtered list of applications for the tracker page.
        Returns (page_of_jobs, total_filtered_count, aggregations_dict).
        """
        # ── Base query: Optimized to query user_id directly ───────────────────
        base_stmt = (
            select(JobOfferDB)
            .where(JobOfferDB.user_id == UUID(user_id))
            .where(JobOfferDB.status == status)
        )

        # ── Total unfiltered (before dynamic filters) ────────────────────────
        total_unfiltered_result = await self.session.execute(
            select(func.count(JobOfferDB.id))
            .where(JobOfferDB.user_id == UUID(user_id))
            .where(JobOfferDB.status == status)
        )
        total_unfiltered = total_unfiltered_result.scalar_one()

        # ── Dynamic filters ───────────────────────────────────────────────────
        if filters:
            if company := filters.get('company'):
                base_stmt = base_stmt.where(
                    JobOfferDB.company_name.ilike(f"%{company}%")
                )
            if title := filters.get('title'):
                base_stmt = base_stmt.where(
                    JobOfferDB.job_title.ilike(f"%{title}%")
                )
            if location := filters.get('location'):
                base_stmt = base_stmt.where(
                    JobOfferDB.location.ilike(f"%{location}%")
                )
            if board := filters.get('board'):
                base_stmt = base_stmt.where(
                    JobOfferDB.job_board == board
                )
            if date_from := filters.get('date_from'):
                base_stmt = base_stmt.where(
                    func.date(JobOfferDB.application_date) >= date_from
                )
            if date_to := filters.get('date_to'):
                base_stmt = base_stmt.where(
                    func.date(JobOfferDB.application_date) <= date_to
                )
            if (has_resp := filters.get('has_response')) is not None:
                base_stmt = base_stmt.where(JobOfferDB.has_response == has_resp)
            if (has_int := filters.get('has_interview')) is not None:
                base_stmt = base_stmt.where(JobOfferDB.has_interview == has_int)

        # ── Total after filters (for pagination metadata) ────────────────────
        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total_filtered = (await self.session.execute(count_stmt)).scalar_one()

        # ── Top 3 job titles from filtered results ───────────────────────────
        titles_stmt = (
            select(JobOfferDB.job_title, func.count(JobOfferDB.id).label("cnt"))
            .select_from(base_stmt.subquery())
            .group_by(JobOfferDB.job_title)
            .order_by(func.count(JobOfferDB.id).desc())
            .limit(3)
        )
        titles_result = await self.session.execute(titles_stmt)
        top_titles = [
            {"name": row.job_title, "value": row.cnt}
            for row in titles_result.all()
        ]

        aggregations = {
            "total_unfiltered": total_unfiltered,
            "top_titles": top_titles,
        }

        # ── Sort + paginate ───────────────────────────────────────────────────
        page = pagination.get('page', 1)
        limit = pagination.get('limit', 12)
        offset = (page - 1) * limit

        paginated_stmt = (
            base_stmt
            .order_by(JobOfferDB.application_date.desc())
            .offset(offset)
            .limit(limit)
        )
        offers_result = await self.session.execute(paginated_stmt)
        offers = [self._map_to_entity(o) for o in offers_result.scalars().all()]

        return offers, total_filtered, aggregations

    # =========================================================================
    # DASHBOARD: ANALYTICS
    # =========================================================================

    async def get_analytics(
        self,
        user_id: str,
        period: str,
        status: ApplicationStatus = ApplicationStatus.SUBMITTED
    ) -> dict:
        """
        Aggregated stats for the analytics page.
        Returns totals, period counts, response/interview rates, and top locations.
        """
        now = datetime.now(timezone.utc)

        # ── Define period window ──────────────────────────────────────────────
        start_date = None
        if period == 'last_week':
            start_date = now - timedelta(days=7)
        elif period == 'last_month':
            start_date = now - timedelta(days=30)

        # ── Base: all submitted jobs for this user ────────────────────────────
        base_stmt = (
            select(JobOfferDB)
            # ✅ OPTIMIZED: Removed join(JobSearchDB), querying user_id directly
            .where(JobOfferDB.user_id == UUID(user_id))
            .where(JobOfferDB.status == status)
        )

        # ── Total applications (all time, no period filter) ───────────────────
        total_result = await self.session.execute(
            select(func.count(JobOfferDB.id)).select_from(base_stmt.subquery())
        )
        total_applications = total_result.scalar_one()

        # ── Period-filtered base ──────────────────────────────────────────────
        period_stmt = base_stmt
        if start_date:
            period_stmt = period_stmt.where(JobOfferDB.application_date >= start_date)

        # ── Period count + response/interview counts in one query ─────────────
        agg_result = await self.session.execute(
            select(
                func.count(JobOfferDB.id).label("period_count"),
                func.sum(
                    case((JobOfferDB.has_response is True, 1), else_=0)
                ).label("response_count"),
                func.sum(
                    case((JobOfferDB.has_interview is True, 1), else_=0)
                ).label("interview_count"),
            ).select_from(period_stmt.subquery())
        )
        agg_row = agg_result.one()
        period_applications = agg_row.period_count or 0
        responses = agg_row.response_count or 0
        interviews = agg_row.interview_count or 0

        # ── Top 7 locations from period results ───────────────────────────────
        locations_stmt = (
            select(
                JobOfferDB.location,
                func.count(JobOfferDB.id).label("cnt")
            )
            .select_from(period_stmt.subquery())
            .group_by(JobOfferDB.location)
            .order_by(func.count(JobOfferDB.id).desc())
            .limit(7)
        )
        locations_result = await self.session.execute(locations_stmt)
        by_location = [
            {"name": row.location, "count": row.cnt}
            for row in locations_result.all()
        ]

        return {
            "total_applications": total_applications,
            "period_applications": period_applications,
            "responses": responses,
            "interviews": interviews,
            "by_location": by_location,
        }

    # =========================================================================
    # MAPPER
    # =========================================================================

    def _map_to_entity(self, offer_db: JobOfferDB) -> JobOffer:
        offer = JobOffer(
            url=offer_db.url,
            form_url=offer_db.form_url,
            search_id=offer_db.search_id,
            user_id=offer_db.user_id, # ✅ FIXED: Added missing user_id
            company_name=offer_db.company_name,
            job_title=offer_db.job_title,
            location=offer_db.location,
            job_board=offer_db.job_board,
            cover_letter=offer_db.cover_letter,
            ranking=offer_db.ranking,
            job_desc=offer_db.job_desc,
            application_date=offer_db.application_date,
            followup_date=offer_db.followup_date,
            status=offer_db.status,
            has_interview=offer_db.has_interview,
            has_response=offer_db.has_response,
        )
        offer.id = offer_db.id
        # Restore private field directly — bypasses dataclass init restriction
        object.__setattr__(offer, '_job_posting_id', offer_db.job_posting_id)
        return offer