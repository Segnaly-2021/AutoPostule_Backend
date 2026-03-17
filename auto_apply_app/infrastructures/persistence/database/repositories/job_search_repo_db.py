# =============================================================================
# job_search_repo_db.py
# =============================================================================
from uuid import UUID
from typing import List
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.application.repositories.job_search_repo import JobSearchRepository
from auto_apply_app.infrastructures.persistence.database.models.schema import JobSearchDB, JobOfferDB
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
        """Upsert the search metadata."""
        search_db = JobSearchDB(
            id=search.id,
            user_id=search.user_id,
            job_title=search.job_title,
            job_boards=search.job_boards,  # ✅ FIXED: plural
            search_status=search.search_status,
            contract_types=search.contract_types,
            min_salary=search.min_salary,
            location=search.location,
            updated_at=search.updated_at,
        )
        await self.session.merge(search_db)

    # ✅ ADDED: Missing method from interface
    async def get_all_jobs(self) -> List[JobOffer]:
        result = await self.session.execute(select(JobOfferDB))
        return [self._map_offer_to_entity(offer_db) for offer_db in result.scalars().all()]

    # ✅ ADDED: Missing method from interface (Critical for saving matched jobs)
    async def save_all_jobs(self, search: JobSearch) -> None:
        # First, ensure the search itself is saved
        await self.save(search)
        
        # Then, merge all the nested jobs
        for job in search.all_matched_jobs:
            offer_db = JobOfferDB(
                id=job.id,
                search_id=search.id,
                user_id=job.user_id,
                url=job.url,
                form_url=job.form_url,
                company_name=job.company_name,
                job_title=job.job_title,
                location=job.location,
                job_board=job.job_board,
                job_posting_id=job._job_posting_id,
                cover_letter=job.cover_letter,
                ranking=job.ranking,
                job_desc=job.job_desc,
                application_date=job.application_date,
                followup_date=job.followup_date,
                status=job.status,
                has_interview=job.has_interview,
                has_response=job.has_response,
            )
            await self.session.merge(offer_db)

    async def update(self, search_id: UUID, search_domain: JobSearch) -> None:
        result = await self.session.execute(
            select(JobSearchDB).where(JobSearchDB.id == search_id)
        )
        search_db = result.scalar_one_or_none()
        if search_db is None:
            raise JobSearchNotFoundError(f"Job search {search_id} not found")

        # ✅ FIXED: job_boards plural
        for key in ("job_title", "job_boards", "search_status", "contract_types",
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
        search = JobSearch(
            user_id=search_db.user_id,
            job_title=search_db.job_title,
            job_boards=search_db.job_boards, # ✅ FIXED: plural
            _matched_jobs=matched_jobs,
            search_status=search_db.search_status,
            contract_types=search_db.contract_types,
            min_salary=search_db.min_salary,
            location=search_db.location,
            updated_at=search_db.updated_at,
        )

        search.id = search_db.id

        return search

    def _map_offer_to_entity(self, offer_db) -> JobOffer:
        offer = JobOffer(
            url=offer_db.url,
            form_url=offer_db.form_url,
            search_id=offer_db.search_id,
            user_id=offer_db.user_id, # ✅ ADDED: Missing user_id
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
        object.__setattr__(offer, '_job_posting_id', offer_db.job_posting_id)
        return 
    

    
    async def delete_job(self, job_id: UUID) -> None:
        """Delete a specific job offer from the database."""
        await self.session.execute(
            delete(JobOfferDB).where(JobOfferDB.id == job_id)
        )