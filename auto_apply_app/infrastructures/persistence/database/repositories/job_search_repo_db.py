
# =============================================================================
# job_search_repo_db.py
# =============================================================================
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.application.repositories.job_search_repo import JobSearchRepository
from auto_apply_app.infrastructures.persistence.database.models.schema import JobSearchDB
from auto_apply_app.domain.exceptions import JobSearchNotFoundError


class JobSearchRepoDB(JobSearchRepository):
    IMMUTABLE_FIELDS = frozenset({"id", "user_id"})

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, search_id: UUID) -> JobSearch:
        result = await self.session.execute(
            select(JobSearchDB)
            .options(selectinload(JobSearchDB.job_offers))
            .where(JobSearchDB.id == search_id)
        )
        search_db = result.scalar_one_or_none()
        if search_db is None:
            raise JobSearchNotFoundError(f"Job search {search_id} not found")
        return self._map_to_entity(search_db)

    async def save(self, search: JobSearch) -> None:
        """Upsert."""
        search_db = JobSearchDB(
            id=search.id,
            user_id=search.user_id,
            job_title=search.job_title,
            job_board=search.job_board,
            search_status=search.search_status,
            contract_types=search.contract_types,
            min_salary=search.min_salary,
            location=search.location,
            updated_at=search.updated_at,
        )
        await self.session.merge(search_db)

    async def update(self, search_id: UUID, search_domain: JobSearch) -> None:
        result = await self.session.execute(
            select(JobSearchDB).where(JobSearchDB.id == search_id)
        )
        search_db = result.scalar_one_or_none()
        if search_db is None:
            raise JobSearchNotFoundError(f"Job search {search_id} not found")

        # Only update mutable scalar fields
        for key in ("job_title", "job_board", "search_status", "contract_types",
                    "min_salary", "location", "updated_at"):
            value = getattr(search_domain, key, None)
            if value is not None:
                setattr(search_db, key, value)

    async def delete(self, search_id: UUID) -> None:
        result = await self.session.execute(
            select(JobSearchDB).where(JobSearchDB.id == search_id)
        )
        search_db = result.scalar_one_or_none()
        if search_db is None:
            raise JobSearchNotFoundError(f"Job search {search_id} not found")
        await self.session.delete(search_db)

    def _map_to_entity(self, search_db: JobSearchDB) -> JobSearch:
        matched_jobs = {
            offer_db.id: self._map_offer_to_entity(offer_db)
            for offer_db in search_db.job_offers
        }
        return JobSearch(
            id=search_db.id,
            user_id=search_db.user_id,
            job_title=search_db.job_title,
            job_board=search_db.job_board,
            _matched_jobs=matched_jobs,
            search_status=search_db.search_status,
            contract_types=search_db.contract_types,
            min_salary=search_db.min_salary,
            location=search_db.location,
            updated_at=search_db.updated_at,
        )

    def _map_offer_to_entity(self, offer_db) -> JobOffer:
        offer = JobOffer(
            id=offer_db.id,
            url=offer_db.url,
            form_url=offer_db.form_url,
            search_id=offer_db.search_id,
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
        object.__setattr__(offer, '_job_posting_id', offer_db.job_posting_id)
        return offer