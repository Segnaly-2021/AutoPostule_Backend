from dataclasses import dataclass
from pydantic import EmailStr

from auto_apply_app.interfaces.viewmodels.base import OperationResult
from auto_apply_app.application.common.result import Result
from auto_apply_app.interfaces.presenters.base_presenter import UserPresenter
from auto_apply_app.application.use_cases.user_use_cases import (
    RegisterUserUseCase,
    LoginUserUseCase,
    LogoutUseCase,
    ChangePasswordUseCase,
    RequestPasswordResetUseCase,    # ✅ Added
    ConfirmPasswordResetUseCase,
    ResendVerificationEmailUseCase,
    VerifyEmailUseCase     # ✅ Added
)
from auto_apply_app.application.dtos.auth_user_dtos import (
    RegisterUserRequest,
    LoginRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest,          # ✅ Added
    ResetPasswordRequest            # ✅ Added
)

@dataclass
class AuthController:
    register_use_case: RegisterUserUseCase
    login_use_case: LoginUserUseCase
    logout_use_case: LogoutUseCase
    change_password_use_case: ChangePasswordUseCase
    verify_email_use_case: VerifyEmailUseCase                    # NEW
    resend_verification_use_case: ResendVerificationEmailUseCase # NEW
    request_password_reset_use_case: RequestPasswordResetUseCase  # ✅ Added
    confirm_password_reset_use_case: ConfirmPasswordResetUseCase  # ✅ Added
    presenter: UserPresenter

    async def handle_register(
        self, 
        email: EmailStr, 
        password: str, 
        firstname: str, 
        lastname: str
    ) -> OperationResult:
        try:
            request = RegisterUserRequest(
                auth_email=email,
                auth_password=password,
                firstname=firstname,
                lastname=lastname
            )
            result = await self.register_use_case.execute(request)
            
            if result.is_success:
                view_model = self.presenter.present_user(result.value)
                return OperationResult.succeed(value=view_model)
            
            return self._present_error(result)

        except ValueError as e:
            return self._present_validation_exception(e)

    async def handle_login(self, email: EmailStr, password: str) -> OperationResult:
        try:
            request = LoginRequest(auth_email=email, auth_password=password)
            result = await self.login_use_case.execute(request)

            if result.is_success:
                # result.value is LoginResponse, mapped to LoginViewModel
                view_model = self.presenter.present_login(result.value)
                return OperationResult.succeed(value=view_model)
            
            return self._present_error(result)

        except ValueError as e:
            return self._present_validation_exception(e)

    async def handle_logout(self, token: str) -> OperationResult:
        # Logout is unique: it fails silently in the use case if token is invalid
        await self.logout_use_case.execute(token)
        return OperationResult.succeed(value={"message": "Successfully logged out"})

    async def handle_change_password(
        self, 
        user_id: str, 
        old_password: str, 
        new_password: str
    ) -> OperationResult:
        try:
            request = ChangePasswordRequest(
                user_id=user_id,
                old_password=old_password,
                new_password=new_password
            )
            result = await self.change_password_use_case.execute(request)

            if result.is_success:
                # If use case returns a string, wrap it. If it returns dict, use result.value directly.
                val = {"message": result.value} if isinstance(result.value, str) else result.value
                return OperationResult.succeed(value=val)
            
            return self._present_error(result)

        except ValueError as e:
            return self._present_validation_exception(e)

    # --- NEW: Forgot Password Flow ---
    
    async def handle_forgot_password(self, email: EmailStr) -> OperationResult:
        try:
            request = ForgotPasswordRequest(email=email)
            result = await self.request_password_reset_use_case.execute(request)
            
            if result.is_success:
                # result.value is already a dictionary: {"message": "..."}
                return OperationResult.succeed(value=result.value)
                
            return self._present_error(result)
            
        except ValueError as e:
            return self._present_validation_exception(e)
        
        

    async def handle_reset_password(self, token: str, new_password: str) -> OperationResult:
        try:
            request = ResetPasswordRequest(token=token, new_password=new_password)
            result = await self.confirm_password_reset_use_case.execute(request)
            
            if result.is_success:
                # result.value is already a dictionary: {"message": "..."}
                return OperationResult.succeed(value=result.value)
                
            return self._present_error(result)
            
        except ValueError as e:
            return self._present_validation_exception(e)
    

    async def handle_verify_email(self, token: str) -> OperationResult:
        try:
            result = await self.verify_email_use_case.execute(token)
            if result.is_success:
                view_model = self.presenter.present_message(result.value)
                return OperationResult.succeed(value=view_model)
            return self._present_error(result)
        except ValueError as e:
            return self._present_validation_exception(e)


    async def handle_resend_verification(self, email: str) -> OperationResult:
        try:
            result = await self.resend_verification_use_case.execute(email)
            if result.is_success:
                view_model = self.presenter.present_message(result.value)
                return OperationResult.succeed(value=view_model)
            return self._present_error(result)
        except ValueError as e:
            return self._present_validation_exception(e)

        

    # --- Private Error Mapping Helpers ---

    def _present_error(self, result: Result) -> OperationResult:
        error_vm = self.presenter.present_error(
            result.error.message, 
            str(result.error.code.name)
        )
        return OperationResult.fail(error_vm.message, error_vm.code)

    def _present_validation_exception(self, e: ValueError) -> OperationResult:
        error_vm = self.presenter.present_error(str(e), "VALIDATION_ERROR")
        return OperationResult.fail(error_vm.message, error_vm.code)