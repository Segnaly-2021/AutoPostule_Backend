from typing import Dict, List, Optional, Set, Tuple
from uuid import UUID
from datetime import datetime, timezone, timedelta
import time
import copy

from auto_apply_app.application.repositories.token_blacklist import TokenBlacklistRepository
from auto_apply_app.application.repositories.auth_repo import AuthRepository
from auto_apply_app.domain.entities.auth_user import AuthUser
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.domain.entities.user import User
from auto_apply_app.domain.entities.user_subscription import UserSubscription
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.value_objects import ApplicationStatus
from auto_apply_app.application.repositories.user_repo import UserRepository
from auto_apply_app.application.repositories.job_offer_repo import JobOfferRepository
from auto_apply_app.application.repositories.job_search_repo import JobSearchRepository
from auto_apply_app.application.repositories.subscription_repo import SubscriptionRepository
from auto_apply_app.domain.exceptions import UserNotFoundError, JobNotFoundError, JobSearchNotFoundError
from auto_apply_app.application.repositories.preferences_repo import UserPreferencesRepository
from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.application.repositories.board_credentials_repo import BoardCredentialsRepository
from auto_apply_app.domain.entities.board_credentials import BoardCredential

class InMemoryUserRepository(UserRepository):
    """In-memory implementation of UserRepository"""

    def __init__(self, storage: Dict[UUID, User]) -> None:

        self._users = storage



    async def get(self, user_id: UUID) -> User:
        """
        Retrieve a user by its ID.

        Args:
            user_id: The unique identifier of the user

        Returns:
            The requested User entity

        Raises:
            UserNotFoundError: If no user exists with the given ID
        """
        if user := self._users.get(user_id):
            return user
        raise UserNotFoundError(user_id)
    
    async def get_by_email(self, email: str) -> User:
        """
        Retrieve a user by their email address.

        Args:
            email: The email address of the user

        Returns:
            The requested User entity

        Raises:
            UserNotFoundError: If no user exists with the given email
        """
        for user in self._users.values():
            if user.email == email:
                return user
        raise UserNotFoundError(f"User with email {email} not found")
  
    async def save(self, user: User) -> None:
        """
        Save a user to the repository.

        Args:
            user: The User entity to save
        """
        self._users[user.id] = user

    async def get_all(self) -> List[User]:
        if len(self._users) == 0:
            return []
        
        return self._users.values()
    
    async def delete(self, user_id: UUID) -> None:
        """
        Delete a user from the repository.

        Args:
            user_id: The unique identifier of the user to delete
        """
        self._users.pop(user_id, None)

    async def update(self, user_id: UUID, data: dict):
        
        user = self._users.get(user_id, None)
        if user is None:
            raise UserNotFoundError(f"User with ID {str(user_id)} not found")        
        
        for key, value in data.items():
            if hasattr(user, key) and value is not None:
                setattr(user, key, value)
        return user  

class InMemoryJobOfferRepository(JobOfferRepository):  
    """
    In-memory implementation of JobOfferRepository.
    Useful for testing and local development.
    """

    def __init__(self, storage: Dict[UUID, JobOffer] = None) -> None:
        self._jobs: Dict[UUID, JobOffer] = storage if storage is not None else {}

    async def get(self, job_id: UUID) -> JobOffer:
        """Retrieve a job by its ID."""
        if job := self._jobs.get(job_id):
            return job
        raise JobNotFoundError(f"Job with ID {job_id} not found.")

    async def save(self, job: JobOffer) -> None:
        """Save a single job."""
        self._jobs[job.id] = job

    async def save_all(self, jobs: List[JobOffer]) -> None:
        """
        Batch save implementation.
        In-memory, this is just a loop, but in SQL this would be a bulk insert.
        """
        for job in jobs:
            self._jobs[job.id] = job

    async def get_total_job(self):
        return len(self._jobs)

    async def get_by_search(self, search_id: UUID, status: Optional[ApplicationStatus] = None) -> List[JobOffer]:
        """
        Retrieve jobs by search_id with optional status filtering.
        """
        if not self._jobs:
            return []
        
        # Filter by search_id
        matching_jobs = [
            job for job in self._jobs.values() 
            if job.search_id == search_id
        ]
        
        # Apply optional status filter
        if status:
            matching_jobs = [
                job for job in matching_jobs
                if job.status == status
            ]
            
        return matching_jobs


    async def get_by_search_and_status(
        self, 
        search_id: str, 
        status: ApplicationStatus
    ) -> List[JobOffer]:
        """
        Get all jobs for a specific search with a specific status.
        
        Args:
            search_id: UUID of the job search
            status: ApplicationStatus enum (e.g., GENERATED, APPROVED)
        
        Returns:
            List of JobOffer entities, sorted by creation date (newest first)
        """
        # Filter jobs by search_id and status
        matching_jobs = [
            job for job in self._jobs.values()
            if str(job.search_id) == search_id and job.status == status
        ]       
        
        return matching_jobs

    async def get_recent_application_hashes(self, user_id: UUID, days: int = 14) -> Set[str]:
        """
        Retrieves hashes of jobs that were SUBMITTED within the last X days.
        Used to prevent duplicate applications.
        """
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        hashes = set()

        for job in self._jobs.values():
            # 1. We only care about jobs that were actually submitted
            if job.status == ApplicationStatus.SUBMITTED and job.application_date:
                # 2. Check time window
                # Ensure application_date is timezone-aware or comparable
                if job.application_date >= cutoff_date:
                    try:
                        # 3. Get the hash (fingerprint)
                        # We assume the job belongs to the user context implicitly in this repo,
                        # or we rely on the hash generation being consistent.
                        hashes.add(job.get_job_posting_id())
                    except Exception:
                        # Handle cases where posting ID isn't set
                        continue
                        
        return hashes

    async def delete(self, job_id: UUID) -> None:
        """Delete a job."""
        self._jobs.pop(job_id, None)

    async def get_user_applications(
        self, 
        user_id: str, 
        filters: dict, 
        pagination: dict,
        status: ApplicationStatus = ApplicationStatus.SUBMITTED
    ) -> Tuple[List[JobOffer], int, dict]:  # ✅ Added dict for aggregations
        
        # 1. Base Filter: Status AND User ID
        all_user_jobs = [
            job for job in self._jobs.values()
            if job.status == status and str(job.user_id).strip() == user_id.strip()
        ]
        
        # Store total before filtering for reference
        total_unfiltered = len(all_user_jobs)
        
        # 2. Apply Dynamic Filters
        filtered_jobs = all_user_jobs
        if filters:
            if company := filters.get('company'):
                filtered_jobs = [j for j in filtered_jobs if company.lower() in j.company_name.lower()]
            
            if title := filters.get('title'):
                filtered_jobs = [j for j in filtered_jobs if title.lower() in j.job_title.lower()]
            
            if location := filters.get('location'):
                filtered_jobs = [j for j in filtered_jobs if location.lower() == j.location.lower()]
            
            if board := filters.get('board'):
                filtered_jobs = [j for j in filtered_jobs if board.lower() == str(j.job_board.name).lower()]
            
            if date_from := filters.get('date_from'):
                filtered_jobs = [j for j in filtered_jobs if j.application_date and j.application_date.date() >= date_from]
            
            if date_to := filters.get('date_to'):
                filtered_jobs = [j for j in filtered_jobs if j.application_date and j.application_date.date() <= date_to]
            
            if (has_resp := filters.get('has_response')) is not None:
                filtered_jobs = [j for j in filtered_jobs if j.has_response == has_resp]
                
            if (has_int := filters.get('has_interview')) is not None:
                filtered_jobs = [j for j in filtered_jobs if j.has_interview == has_int]

        # 3. ✅ Calculate Aggregations on FILTERED data
        # Count top 3 job titles from filtered results
        title_counts = {}
        for j in filtered_jobs:
            t = j.job_title
            title_counts[t] = title_counts.get(t, 0) + 1
        
        sorted_titles = sorted(title_counts.items(), key=lambda item: item[1], reverse=True)[:3]
        top_titles = [{"name": title, "value": count} for title, count in sorted_titles]
        
        aggregations = {
            "total_unfiltered": total_unfiltered,  # All user applications (not affected by filters)
            "top_titles": top_titles  # Top 3 from filtered results
        }

        # 4. Sort (Newest first)
        filtered_jobs.sort(key=lambda x: x.application_date or datetime.min, reverse=True)
        
        # 5. Pagination
        total_filtered = len(filtered_jobs)
        page = pagination.get('page', 1)
        limit = pagination.get('limit', 12)
        start = (page - 1) * limit
        end = start + limit
        
        return filtered_jobs[start:end], total_filtered, aggregations

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

        job = self._jobs.get(uuid_id)
        
        # Note: We usually don't need to check user_id here if the UUID is unique globally,
        # but in a real SQL query you might add `AND user_id = :uid` for security.
        if not job or job.status != status:
            raise JobNotFoundError(f"Job {job_id} not found with status {status.name}")

        job.update_response_status(has_response)
        self._jobs[uuid_id] = job
        return job

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

        job = self._jobs.get(uuid_id)
        
        if not job or job.status != status:
            raise JobNotFoundError(f"Job {job_id} not found with status {status.name}")

        job.update_interview_status(has_interview)
        self._jobs[uuid_id] = job
        return job

    async def get_daily_application_count(self, user_id: str) -> int:
        """
        Get the total number of applications submitted by the user today.
        """
        # Calculate midnight of the current day in UTC
        today_midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        
        count = 0
        for job in self._jobs.values():
            # Ensure safe comparison of user_id
            if str(job.user_id).strip() != user_id.strip():
                continue
                
            # Only count submitted applications
            if job.status != ApplicationStatus.SUBMITTED or not job.application_date:
                continue
                
            # Safely handle naive vs aware datetimes
            job_date = job.application_date
            if job_date.tzinfo is None:
                job_date = job_date.replace(tzinfo=timezone.utc)
                
            # Check if applied today
            if job_date >= today_midnight:
                count += 1
                
        return count

    async def get_analytics(
        self, 
        user_id: str, 
        period: str,
        status: ApplicationStatus = ApplicationStatus.SUBMITTED
    ) -> dict:
        
        now = datetime.now(timezone.utc)
        
        # 1. Filter by User ID and Status first
        all_user_jobs = [
            j for j in self._jobs.values() 
            if j.status == status and str(j.user_id).strip() == user_id.strip()
        ]

        #print(f"[Analytics DEBUG] After user id + status Filtering: {len(all_user_jobs)}")

        # 2. Define Time Window
        start_date = None
        if period == 'last_week':
            start_date = now - timedelta(days=7)
        elif period == 'last_month':
            start_date = now - timedelta(days=30)

        # 3. Filter for Period
        period_jobs = all_user_jobs
        if start_date:
            period_jobs = [
                j for j in all_user_jobs
                if j.application_date and 
                (j.application_date.replace(tzinfo=timezone.utc) if j.application_date.tzinfo is None else j.application_date) >= start_date
            ]

        # 4. Aggregate
        responses = sum(1 for j in period_jobs if j.has_response)
        interviews = sum(1 for j in period_jobs if j.has_interview)

        #print(f"[Analytics DEBUG] After date Filtering: {len(period_jobs)}, responses: {responses}, interviews: {interviews}")
        
        # Count by board
        # by_board = {}
        # for j in period_jobs:
        #     board_name = j.job_board.name if hasattr(j.job_board, 'name') else str(j.job_board).lower()
        #     by_board[board_name] = by_board.get(board_name, 0) + 1

        # ✅ NEW: Count by location instead of titles
        location_counts = {}
        for j in period_jobs:
            loc = j.location
            location_counts[loc] = location_counts.get(loc, 0) + 1
        
        # Get top 7 locations
        sorted_locations = sorted(location_counts.items(), key=lambda item: item[1], reverse=True)[:7]
        by_location_list = [{"name": location, "count": count} for location, count in sorted_locations]

        return {
            "total_applications": len(all_user_jobs),
            "period_applications": len(period_jobs),
            "responses": responses,
            "interviews": interviews,
            "by_location": by_location_list  # Changed from by_title
        }
    

class InMemoryJobSearchRepository(JobSearchRepository):   
    """
    In-memory implementation of JobSearchRepository.
    Matches the async abstract interface.
    """

    def __init__(self, storage: Dict[UUID, JobSearch], job_storage: Dict[UUID, JobOffer]) -> None:
        # Stores the JobSearch entities
        self._searches: Dict[UUID, JobSearch] = storage
        self._job_repo = InMemoryJobOfferRepository(job_storage)

    async def get(self, search_id: UUID) -> JobSearch:
                
        # Debug exact types
        for k in self._searches.keys():
            print(f"Key: {repr(k)} (Type: {type(k)}) == Target: {repr(search_id)} (Type: {type(search_id)})")
        
        search = self._searches.get(search_id)
        # ...
        if search is None:
            raise JobSearchNotFoundError(search_id)
        return search
    

    async def save(self, search: JobSearch) -> None:
        """
        Save a search to the repository.

        Args:
            search: The JobSearch entity to save
        """
        self._searches[search.id] = search
        

    async def get_all_jobs(self) -> List[JobOffer]:
        """
        Retrieve all matched Jobs across all searches.
        (Or specific to an internal state if required by your logic).
        """
        all_jobs = []
        for search in self._searches.values():
            all_jobs.extend(search.all_matched_jobs)
        return all_jobs

    async def save_all_jobs(self, search: JobSearch) -> None:
        """
        Persists the JobSearch entity, which contains the matched jobs.
        """
        # In-memory, we simply store the object reference
        self._searches[search.id] = search

    async def delete_job(self, job_id: UUID) -> None:
        """
        Delete a specific job from any search it might belong to.
        """
        for search in self._searches.values():
            # Assuming JobSearch entity has a method or attribute to remove jobs
            search.all_matched_jobs = [
                job for job in search.all_matched_jobs 
                if job.id != job_id
            ]

class InMemoryPreferencesRepository(UserPreferencesRepository):
    """In-memory implementation of UserPreferencesRepository"""
    
    def __init__(self, storage: Dict[UUID, UserPreferences] = None):
        self._prefs: Dict[UUID, UserPreferences] = storage if storage is not None else {}
    
    async def get_by_user_id(self, user_id: UUID) -> Optional[UserPreferences]:
        """Find preferences by user_id (not by preferences id)"""
        for pref in self._prefs.values():
            if pref.user_id == user_id:
                return pref
        return None
    
    async def save(self, preferences: UserPreferences) -> None:
        """Save or update preferences"""
        # Ensure ID exists
        if not preferences.id:
            from uuid import uuid4
            preferences.id = uuid4()
        
        self._prefs[preferences.id] = preferences
    
    async def delete(self, user_id: UUID) -> None:
        """Delete preferences for a user"""
        # Find the preference entry
        pref_to_delete = None
        for pref_id, pref in self._prefs.items():
            if pref.user_id == user_id:
                pref_to_delete = pref_id
                break
        
        if pref_to_delete:
            self._prefs.pop(pref_to_delete, None)


class InMemoryCredentialsRepository(BoardCredentialsRepository):
    """In-memory implementation of BoardCredentialsRepository"""
    
    def __init__(self, storage: Dict[UUID, BoardCredential] = None):
        self._credentials: Dict[UUID, BoardCredential] = storage if storage is not None else {}
    
    async def get_by_user_and_board(
        self, 
        user_id: UUID, 
        board_name: str
    ) -> Optional[BoardCredential]:
        """Find credential by user_id and board_name"""
        for cred in self._credentials.values():
            if cred.user_id == user_id and cred.job_board.lower() == board_name.lower():
                return cred
        return None
    
    async def get_all_by_user(self, user_id: UUID) -> List[BoardCredential]:
        """Get all credentials for a user"""
        return [
            cred for cred in self._credentials.values()
            if cred.user_id == user_id
        ]
    
    async def save(self, credential: BoardCredential) -> None:
        """Save or update credential"""
        if not credential.id:
            from uuid import uuid4
            credential.id = uuid4()
        
        self._credentials[credential.id] = credential
    
    async def delete(self, user_id: UUID, board_name: str) -> None:
        """Delete credentials for a specific board"""
        cred_to_delete = None
        for cred_id, cred in self._credentials.items():
            if cred.user_id == user_id and cred.job_board.lower() == board_name.lower():
                cred_to_delete = cred_id
                break
        
        if cred_to_delete:
            self._credentials.pop(cred_to_delete, None)
    
    async def delete_all_by_user(self, user_id: UUID) -> None:
        """Delete all credentials for a user"""
        to_delete = [
            cred_id for cred_id, cred in self._credentials.items()
            if cred.user_id == user_id
        ]
        
        for cred_id in to_delete:
            self._credentials.pop(cred_id, None)
   
class InMemoryTokenBlacklistRepository(TokenBlacklistRepository):
    def __init__(self):
        # Stores { "jti_string": expiration_timestamp_float }
        self._blacklist: Dict[str, float] = {}

    async def blacklist_token(self, token_id: str, ttl_seconds: int) -> None:
        expiration_time = time.time() + ttl_seconds
        self._blacklist[token_id] = expiration_time
        await self._cleanup() # Optional: Run a quick cleanup on every write

    async def is_blacklisted(self, token_id: str) -> bool:
        if token_id not in self._blacklist:
            return False
            
        # Check if the ban has expired
        expiration_time = self._blacklist[token_id]
        if time.time() > expiration_time:
            # The token would have expired naturally by now, so we can remove it
            del self._blacklist[token_id]
            return False
            
        return True

    async def _cleanup(self):
        """Removes expired entries to prevent memory leaks."""
        now = time.time()
        # Create a list of keys to remove (cannot modify dict while iterating)
        expired_keys = [k for k, v in self._blacklist.items() if v < now]
        for k in expired_keys:
            del self._blacklist[k]


class InMemoryAuthRepository(AuthRepository):
    def __init__(self, storage: Dict[UUID, AuthUser] = None):
        # Default to empty dict if None provided
        self._storage = storage 

    async def save(self, auth_user: AuthUser) -> None:
        # Use user_id (the Domain User ID) as the primary key for Auth
        self._storage[auth_user.user_id] = auth_user

    async def get_by_email(self, email: str) -> Optional[AuthUser]:
        # Linear search for the email
        for user in self._storage.values():
            if user.email == email:
                return user
        return None  # Ensure it returns None so Use Case can handle it

    async def get_by_id(self, user_id: str) -> Optional[AuthUser]:
        return self._storage.get(UUID(user_id), None)
    

class InMemorySubscriptionRepository(SubscriptionRepository):
    """In-memory implementation of SubscriptionRepository"""

    def __init__(self, storage: Dict[UUID, UserSubscription]) -> None:
        self._subscriptions = storage

    async def get_by_user_id(self, user_id: str) -> Optional[UserSubscription]:
        subs = self._subscriptions.get(UUID(user_id), None)
        if subs is None:
            print(f"""
                  [SubscriptionRepo DEBUG] No subscription found for user_id: {user_id}\n
                  Because user_id not in the DB: {UUID(user_id) not in list(self._subscriptions.keys())}
                  """)
            print(f"[SubscriptionRepo DEBUG] Current subscription keys: {list(self._subscriptions.keys())}")
            print([ sub.user_id for sub in self._subscriptions.values() if isinstance(sub.user_id, UUID) ])
        return subs

    async def get_by_stripe_id(self, stripe_subscription_id: str) -> Optional[UserSubscription]:
        if not stripe_subscription_id:
            return None
        return next(
            (s for s in self._subscriptions.values() 
             if s.stripe_subscription_id == stripe_subscription_id), 
            None
        )

    async def get_by_customer_id(self, stripe_customer_id: str) -> Optional[UserSubscription]:
        if not stripe_customer_id:
            return None
        return next(
            (s for s in self._subscriptions.values() 
             if s.stripe_customer_id == stripe_customer_id), 
            None
        )

    async def save(self, subscription: UserSubscription) -> None:
        self._subscriptions[subscription.user_id] = subscription


class InMemoryUnitOfWork(UnitOfWork):
    # ✅ Class-level shared storage (acts like a persistent database)
    _shared_users_db: Dict[UUID, any] = {}
    _shared_auth_db: Dict[UUID, any] = {}
    _shared_subs_db: Dict[UUID, any] = {}
    _shared_jobs_db: Dict[UUID, any] = {}
    _shared_searchs_db: Dict[UUID, any] = {}

    _shared_prefs_db: Dict[UUID, any] = {}
    _shared_creds_db: Dict[UUID, any] = {}

    def __init__(self):
        # Reference the shared class-level storage
        self._users_db = self.__class__._shared_users_db
        self._auth_db = self.__class__._shared_auth_db
        self._subs_db = self.__class__._shared_subs_db
        self._jobs_db = self.__class__._shared_jobs_db
        self._searchs_db = self.__class__._shared_searchs_db

        self._prefs_db = self.__class__._shared_prefs_db
        self._creds_db = self.__class__._shared_creds_db

        # Snapshots for rollback
        self._users_snapshot = {}
        self._auth_snapshot = {}
        self._subs_snapshot = {}
        self._jobs_snapshot = {}
        self._searchs_snapshot = {}

        # [NEW] Snapshots
        self._prefs_snapshot = {}
        self._creds_snapshot = {}

    async def __aenter__(self):
        # Debug logging
        print(f"[UoW DEBUG] Entering context - Jobs DB size: {len(self._jobs_db)}")
        print(f"[UoW DEBUG] Jobs DB ID: {id(self._jobs_db)}")
        
        # 1. Take snapshots
        self._users_snapshot = copy.deepcopy(self._users_db)
        self._auth_snapshot = copy.deepcopy(self._auth_db)
        self._subs_snapshot = copy.deepcopy(self._subs_db)
        self._jobs_snapshot = copy.deepcopy(self._jobs_db)
        self._searchs_snapshot = copy.deepcopy(self._searchs_db)

        # [NEW] Take snapshots for new repos
        self._prefs_snapshot = copy.deepcopy(self._prefs_db)
        self._creds_snapshot = copy.deepcopy(self._creds_db)

        # 2. Initialize repos with SHARED storage
        self.user_repo = InMemoryUserRepository(self._users_db)
        self.auth_repo = InMemoryAuthRepository(self._auth_db)
        self.subscription_repo = InMemorySubscriptionRepository(self._subs_db)
        self.job_repo = InMemoryJobOfferRepository(self._jobs_db)
        self.search_repo = InMemoryJobSearchRepository(self._searchs_db, self._jobs_db)
        
        # [NEW] Initialize new repos
        self.user_pref_repo = InMemoryPreferencesRepository(self._prefs_db)
        self.board_cred_repo = InMemoryCredentialsRepository(self._creds_db)

        return self

    async def commit(self):
        # Committing in memory simply means we discard the snapshots.
        # The changes were already made to the _shared dicts by the repos.
        self._users_snapshot = {}
        self._auth_snapshot = {}
        self._subs_snapshot = {}
        self._jobs_snapshot = {}
        self._searchs_snapshot = {}
        
        # [NEW] Clear new snapshots
        self._prefs_snapshot = {}
        self._creds_snapshot = {}

    async def rollback(self):
        print("[UoW DEBUG] Rolling back")
        
        # Restore from snapshots
        self._users_db.clear()
        self._users_db.update(self._users_snapshot)
        
        self._auth_db.clear()
        self._auth_db.update(self._auth_snapshot)

        self._subs_db.clear()
        self._subs_db.update(self._subs_snapshot)

        self._jobs_db.clear()
        self._jobs_db.update(self._jobs_snapshot)

        self._searchs_db.clear()
        self._searchs_db.update(self._searchs_snapshot)
        
        # [NEW] Restore new repos
        self._prefs_db.clear()
        self._prefs_db.update(self._prefs_snapshot)
        
        self._creds_db.clear()
        self._creds_db.update(self._creds_snapshot)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            print(f"[UoW DEBUG] Exception occurred: {exc_type.__name__}")
            await self.rollback()
        else:
            await self.commit()
            
    @classmethod
    def reset_all(cls):
        """Utility method to clear all data (useful for tests)"""
        cls._shared_users_db.clear()
        cls._shared_auth_db.clear()
        cls._shared_subs_db.clear()
        cls._shared_jobs_db.clear()
        cls._shared_searchs_db.clear()
        
        # [NEW] Clear new shared DBs
        cls._shared_prefs_db.clear()
        cls._shared_creds_db.clear()