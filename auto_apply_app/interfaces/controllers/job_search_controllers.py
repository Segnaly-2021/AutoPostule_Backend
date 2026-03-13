from dataclasses import dataclass
from typing import List, Optional

from auto_apply_app.interfaces.viewmodels.base import OperationResult
from auto_apply_app.interfaces.viewmodels.job_search_vm import JobSearchViewModel
from auto_apply_app.application.dtos.operations import DeletionOutcome
from auto_apply_app.interfaces.presenters.base_presenter import JobSearchPresenter
from auto_apply_app.application.use_cases.job_search_use_cases import (
    CreateJobSearchUseCase,
    GetJobSearchUseCase,
    UpdateJobSearchUseCase,
    CompleteJobSearchUseCase,
    DeleteJobSearchUseCase,
)
from auto_apply_app.application.dtos.job_search_dtos import (
    CreateJobSearchRequest,
    GetJobSearchRequest,
    UpdateJobSearchRequest,
)


@dataclass
class JobSearchController:

    create_job_search_use_case: CreateJobSearchUseCase
    get_job_search_use_case: GetJobSearchUseCase
    update_job_search_use_case: UpdateJobSearchUseCase
    complete_job_search_use_case: CompleteJobSearchUseCase
    delete_job_search_use_case: DeleteJobSearchUseCase
    job_search_presenter: JobSearchPresenter

    # ---------- CREATE ----------

    def handle_create(
        self,
        job_title: str,
        job_seeker_id: str,
        contract_types: Optional[List[str]] = None,
    ) -> OperationResult[JobSearchViewModel]:

        try:
            request = CreateJobSearchRequest(
                job_title=job_title,
                job_seeker_id=job_seeker_id,
                contract_types=contract_types or [],
            )

            result = self.create_job_search_use_case.execute(request)

            if result.is_success:
                vm = self.job_search_presenter.present_search(result.value)
                return OperationResult.succeed(value=vm)

            error_vm = self.job_search_presenter.present_error(
                result.error.message,
                str(result.error.code.name),
            )
            return OperationResult.fail(error_vm.message, error_vm.code)

        except ValueError as e:
            error_vm = self.job_search_presenter.present_error(
                str(e), "VALIDATION_ERROR"
            )
            return OperationResult.fail(error_vm.message, error_vm.code)

    # ---------- GET ----------

    def handle_get(
        self, job_search_id: str
    ) -> OperationResult[JobSearchViewModel]:

        try:
            request = GetJobSearchRequest(job_search_id)
            result = self.get_job_search_use_case.execute(request)

            if result.is_success:
                vm = self.job_search_presenter.present_search(result.value)
                return OperationResult.succeed(value=vm)

            error_vm = self.job_search_presenter.present_error(
                result.error.message,
                str(result.error.code.name),
            )
            return OperationResult.fail(error_vm.message, error_vm.code)

        except ValueError as e:
            error_vm = self.job_search_presenter.present_error(
                str(e), "VALIDATION_ERROR"
            )
            return OperationResult.fail(error_vm.message, error_vm.code)

    # ---------- UPDATE ----------

    def handle_update(
        self,
        job_search_id: str,
        job_title: Optional[str] = None,
        status: Optional[str] = None,
        contract_types: Optional[List[str]] = None,
    ) -> OperationResult[JobSearchViewModel]:

        try:
            request = UpdateJobSearchRequest(
                job_search_id=job_search_id,
                job_title=job_title,
                status=status,
                contract_types=contract_types,
            )

            result = self.update_job_search_use_case.execute(request)

            if result.is_success:
                vm = self.job_search_presenter.present_search(result.value)
                return OperationResult.succeed(value=vm)

            error_vm = self.job_search_presenter.present_error(
                result.error.message,
                str(result.error.code.name),
            )
            return OperationResult.fail(error_vm.message, error_vm.code)

        except ValueError as e:
            error_vm = self.job_search_presenter.present_error(
                str(e), "VALIDATION_ERROR"
            )
            return OperationResult.fail(error_vm.message, error_vm.code)

    # ---------- COMPLETE ----------

    def handle_complete(
        self, job_search_id: str
    ) -> OperationResult[JobSearchViewModel]:

        try:
            request = GetJobSearchRequest(job_search_id)
            result = self.complete_job_search_use_case.execute(request)

            if result.is_success:
                vm = self.job_search_presenter.present_search(result.value)
                return OperationResult.succeed(value=vm)

            error_vm = self.job_search_presenter.present_error(
                result.error.message,
                str(result.error.code.name),
            )
            return OperationResult.fail(error_vm.message, error_vm.code)

        except ValueError as e:
            error_vm = self.job_search_presenter.present_error(
                str(e), "VALIDATION_ERROR"
            )
            return OperationResult.fail(error_vm.message, error_vm.code)

    # ---------- DELETE ----------

    def handle_delete(
        self, job_search_id: str
    ) -> OperationResult[DeletionOutcome]:

        try:
            request = GetJobSearchRequest(job_search_id)
            result = self.delete_job_search_use_case.execute(request)

            if result.is_success:
                return OperationResult.succeed(value=result.value)

            error_vm = self.job_search_presenter.present_error(
                result.error.message,
                str(result.error.code.name),
            )
            return OperationResult.fail(error_vm.message, error_vm.code)

        except ValueError as e:
            error_vm = self.job_search_presenter.present_error(
                str(e), "VALIDATION_ERROR"
            )
            return OperationResult.fail(error_vm.message, error_vm.code)
