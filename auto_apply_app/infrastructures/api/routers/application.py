
from fastapi import APIRouter, Depends, Query
from typing import Annotated

#Test
import random
from datetime import datetime, timezone, timedelta
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.value_objects import JobBoard, ApplicationStatus

from auto_apply_app.interfaces.controllers.job_offer_controllers import JobOfferController
from auto_apply_app.infrastructures.api.dependencies.result import handle_result
from auto_apply_app.infrastructures.api.dependencies.auth_deps import CurrentUserId
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.infrastructures.api.schema.job_offer_schema import (
    ApplicationFilters, 
    PaginationParams, 
    StatusUpdateSchema,
    AnalyticsViewModel
)

router = APIRouter()

# Dependency Boilerplate
def get_job_controller(
    app_container: Annotated[Application, Depends(get_container)]
) -> JobOfferController:
    return app_container.job_offer_controller

JobControllerDep = Annotated[JobOfferController, Depends(get_job_controller)]


@router.get(
    "/me",
    summary="Get user applications",
    description="Fetch filtered and paginated applications for current user"
)
async def get_user_applications(
    current_user_id: CurrentUserId,
    controller: JobControllerDep,
    filters: Annotated[ApplicationFilters, Depends()],
    pagination: Annotated[PaginationParams, Depends()]
):
    result = await controller.handle_get_list(
        user_id=current_user_id,
        page=pagination.page, 
        limit=pagination.limit,
        company=filters.company,
        title=filters.title,
        location=filters.location,
        board=filters.board,
        date_from=filters.date_from,
        date_to=filters.date_to,
        has_response=filters.has_response,
        has_interview=filters.has_interview
    )
    return handle_result(result)




@router.patch(
    "/{application_id}/response",
    summary="Toggle response status",
)
async def toggle_response_status(
    application_id: str,
    data: StatusUpdateSchema, # Body: {"status": true}
    current_user_id: CurrentUserId,
    controller: JobControllerDep
):
    # Notice: We ignore current_user_id for logic if the Repo handles ownership check,
    # but strictly we should pass it to EnsureOwnershipUseCase. 
    # For now, we assume the Repo enforces ownership or we pass it down.
    
    result = await controller.handle_toggle_response(
        job_id=application_id, 
        status=data.status
    )
    return handle_result(result)


@router.patch(
    "/{application_id}/interview",
    summary="Toggle interview status",
)
async def toggle_interview_status(
    application_id: str,
    data: StatusUpdateSchema,
    current_user_id: CurrentUserId,
    controller: JobControllerDep
):
    result = await controller.handle_toggle_interview(
        job_id=application_id, 
        status=data.status
    )
    return handle_result(result)


@router.get(
    "/analytics",
    response_model=AnalyticsViewModel,
    summary="Get application analytics",
)
async def get_analytics(
    current_user_id: CurrentUserId,
    controller: JobControllerDep,
    period: str = Query(default='all_time', regex="^(all_time|last_week|last_month)$")
):
    result = await controller.handle_analytics(
        user_id=current_user_id,
        period=period
    )
    return handle_result(result)




# Just for testing
COMPANIES = [
    'TechFlow', 'Creative Inc', 'StartUp AI', 'Big Data Corp', 
    'FinTech Solutions', 'Cloud Networks', 'Green Energy', 'CyberShield',
    'DataWorks', 'AI Innovations', 'DevOps Masters', 'SecureIT',
    'WebCraft', 'CodeFactory', 'Digital Minds', 'NextGen Tech'
]

JOB_TITLES = [
    'Senior Backend Engineer', 'Frontend Developer', 'Product Designer',
    'Data Analyst', 'DevOps Engineer', 'Product Owner', 'Security Analyst',
    'Full Stack Developer', 'ML Engineer', 'QA Engineer', 'UI/UX Designer',
    'Technical Lead', 'Solutions Architect', 'Site Reliability Engineer'
]

LOCATIONS = [
    'Paris', 'Lyon', 'Bordeaux', 'Nantes', 'Remote', 
    'Toulouse', 'Marseille', 'Lille', 'Strasbourg', 'Nice',
    'Rennes', 'Grenoble'
]

# Mapping string names to your Domain Enum
BOARD_MAPPING = {
    'Apec': JobBoard.APEC, 
    'Hellowork': JobBoard.HELLOWORK,
    'WTTJ': JobBoard.WTTJ,
   
}

@router.post(
        "/seed", 
        summary="Generate realistic mock data", 
        status_code=201
)
@router.post("/seed", summary="Generate realistic mock data", status_code=201)
async def seed_data(
    current_user_id: CurrentUserId,
    app_container: Annotated[Application, Depends(get_container)],
    count: int = 100
):
    """
    Seeds the InMemory Database with realistic data matching the frontend mock logic.
    """
    print(f"[SEED DEBUG] Starting seed for user: {current_user_id} (type: {type(current_user_id)})")
    
    async with app_container.uow as uow:
        generated_jobs = []
        
        for i in range(count):
            # 1. Date Logic
            days_ago = int(random.random() * random.random() * 90)
            applied_date = datetime.now(timezone.utc) - timedelta(days=days_ago)
            follow_up_date = applied_date + timedelta(days=7)
            
            # 2. Probability Logic
            has_response = random.random() > 0.75
            has_interview = has_response and (random.random() > 0.7)
            
            # 3. Random Selection
            company = random.choice(COMPANIES)
            title = random.choice(JOB_TITLES)
            location = random.choice(LOCATIONS)
            board_str = random.choice(list(BOARD_MAPPING.keys()))
            board_enum = BOARD_MAPPING[board_str]
            
            # 4. Create Entity
            job = JobOffer(
                url=f"https://mock-job-board.com/{company.lower()}-{i}",
                form_url=f"https://mock-job-board.com/apply/{i}",
                company_name=company,
                job_title=title,
                location=location,
                job_board=board_enum,
                user_id=current_user_id,  # Make sure this is UUID
                status=ApplicationStatus.SUBMITTED,
                
                application_date=applied_date,
                followup_date=follow_up_date,
                
                has_response=has_response,
                has_interview=has_interview,
                
                ranking=random.randint(1, 5),
                job_desc=f"Mock description for {title} at {company}..."
            )
            
            job.set_job_posting_id(current_user_id)
            generated_jobs.append(job)

        print(f"[SEED DEBUG] Generated {len(generated_jobs)} jobs")
        
        # 5. Save Batch INSIDE UoW context
        await uow.job_repo.save_all(generated_jobs)
        
        # Verify save
        total = await uow.job_repo.get_total_job()
        print(f"[SEED DEBUG] Total jobs in repo after save: {total}")
        
        # Sample a job to check user_id
        if generated_jobs:
            sample = generated_jobs[0]
            print(f"[SEED DEBUG] Sample job user_id: {sample.user_id} (type: {type(sample.user_id)})")
        
        return {
            "message": f"Successfully seeded {total} applications for user {current_user_id}",
            "count": total
        }
    

