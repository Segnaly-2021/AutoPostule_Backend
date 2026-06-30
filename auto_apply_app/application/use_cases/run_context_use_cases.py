import logging
from dataclasses import dataclass
from typing import Dict, List, Optional
from uuid import UUID

from auto_apply_app.application.common.result import Result, Error
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.domain.entities.user import User
from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.entities.user_subscription import UserSubscription
from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.domain.entities.board_credentials import BoardCredential
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.value_objects import ApplicationStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StartRunContext:
    user: User
    search: JobSearch
    subscription: UserSubscription
    preferences: UserPreferences
    credentials: Optional[Dict[str, BoardCredential]]


@dataclass(frozen=True)
class ResumeRunContext:
    user: User
    search: JobSearch
    subscription: UserSubscription
    preferences: UserPreferences
    credentials: Optional[Dict[str, BoardCredential]]
    approved_jobs: List[JobOffer]


@dataclass
class LoadStartRunContextUseCase:
    uow: UnitOfWork

    async def execute(self, user_id: UUID, search_id: UUID) -> Result:
        try:
            async with self.uow as uow:
                user = await uow.user_repo.get(user_id)
                if not user:
                    return Result.failure(Error.not_found("User", str(user_id)))
                
                
                print(f"DEBUG: LoadStartRunContextUseCase - type of search_id: {type(search_id)}; value: {search_id}")

                search = await uow.search_repo.get(search_id)
                print(f"DEBUG: LoadStartRunContextUseCase - after get search - type of search: {type(search)}; value: {search}")
                if not search:
                    return Result.failure(Error.not_found("JobSearch", str(search_id)))

                subscription = await uow.subscription_repo.get_by_user_id(str(user_id))
                if not subscription:
                    return Result.failure(Error.not_found("Subscription", str(user_id)))

                preferences = await uow.user_pref_repo.get_by_user_id(user_id)
                if not preferences:
                    preferences = UserPreferences(user_id=user_id)

                credentials = {}
                if preferences.is_full_automation:
                    credentials = {}
                    for board_name in (b for b, v in preferences.active_boards.items() if v):
                        cred = await uow.board_cred_repo.get_by_user_and_board(user_id, board_name)
                        if cred:
                            credentials[board_name] = cred

                return Result.success(StartRunContext(
                    user=user,
                    search=search,
                    subscription=subscription,
                    preferences=preferences,
                    credentials=credentials,
                ))
        except Exception:
            logger.exception("LoadStartRunContextUseCase failed for search %s", search_id)
            return Result.failure(Error.system_error("Could not load run context."))


@dataclass
class LoadResumeRunContextUseCase:
    uow: UnitOfWork

    async def execute(self, user_id: UUID, search_id: UUID, apply_all: bool) -> Result:
        try:
            async with self.uow as uow:
                user = await uow.user_repo.get(user_id)
                if not user:
                    return Result.failure(Error.not_found("User", str(user_id)))

                search = await uow.search_repo.get(search_id)
                if not search:
                    return Result.failure(Error.not_found("JobSearch", str(search_id)))

                subscription = await uow.subscription_repo.get_by_user_id(str(user_id))
                if not subscription:
                    return Result.failure(Error.not_found("Subscription", str(user_id)))

                preferences = await uow.user_pref_repo.get_by_user_id(user_id)
                if not preferences:
                    preferences = UserPreferences(user_id=user_id)

                credentials: Dict[str, BoardCredential] = {}
                if preferences.is_full_automation:
                    for board_name in (b for b, v in preferences.active_boards.items() if v):
                        cred = await uow.board_cred_repo.get_by_user_and_board(user_id, board_name)
                        if cred:
                            credentials[board_name] = cred

                # Drafts were already set to APPROVED by ResumeJobApplicationUseCase.prepare.
                approved_jobs = await uow.job_repo.get_by_search_and_status(
                    str(search_id).strip(), status=ApplicationStatus.APPROVED
                )

                return Result.success(ResumeRunContext(
                    user=user,
                    search=search,
                    subscription=subscription,
                    preferences=preferences,
                    credentials=credentials,
                    approved_jobs=approved_jobs or [],
                ))
        except Exception:
            logger.exception("LoadResumeRunContextUseCase failed for search %s", search_id)
            return Result.failure(Error.system_error("Could not load resume context."))
