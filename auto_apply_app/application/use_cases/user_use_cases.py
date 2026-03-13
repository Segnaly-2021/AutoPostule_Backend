from dataclasses import dataclass
import datetime


from auto_apply_app.application.dtos.operations import DeletionOutcome
from auto_apply_app.application.common.result import Error, Result
from auto_apply_app.domain.entities.user import User
from auto_apply_app.domain.exceptions import (
  UserNotFoundError,
  ValidationError,
  BusinessRuleViolation,
  InvalidTokenException
)
from auto_apply_app.application.dtos.user_dtos import (   
  UpdateUserRequest,
  GetUserRequest,
  UserResponse
)
from auto_apply_app.application.dtos.auth_user_dtos import (
  RegisterUserRequest, 
  LoginResponse, 
  ChangePasswordRequest,
)


from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.application.service_ports.password_service_port import PasswordServicePort
from auto_apply_app.application.dtos.auth_user_dtos import LoginRequest
from auto_apply_app.application.service_ports.token_provider_port import TokenProviderPort
from auto_apply_app.domain.entities.auth_user import AuthUser
from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.domain.entities.user_subscription import UserSubscription

from auto_apply_app.application.repositories.token_blacklist import TokenBlacklistRepository
from auto_apply_app.domain.value_objects import ClientType


@dataclass
class RegisterUserUseCase:
    
    uow: UnitOfWork
    password_service: PasswordServicePort

    async def execute(self, request: RegisterUserRequest) -> Result:
        try:
            
            params = request.to_execution_params()

            # Start the Atomic Transaction
            async with self.uow:
                
                # 1. Validation (Using the repo inside the UoW)
                existing_auth = await self.uow.auth_repo.get_by_email(params["email"])
                if existing_auth:
                    return Result.failure(Error.conflict("User with this email already exists"))

                              
                user = User(                    
                    firstname=params["firstname"],
                    lastname=params["lastname"],
                    email=params["email"],
                    phone_number=None,
                    school_type=None,
                    graduation_year=None,
                    major=None,
                    study_level=None
                )

                raw_password = params.pop("password")
                hashed_password = self.password_service.get_password_hash(raw_password)
                user_id = user.id
                print(f"[RegisterUserUseCase] Generated user_id: {user_id} for email: {params['email']}")

                auth_user = AuthUser(
                    user_id=user_id,
                    email=params["email"],
                    password_hash=hashed_password
                )


                sub_user = UserSubscription(
                    user_id=user_id,
                    email=params["email"], 
                    # Just For Testing
                    account_type=ClientType.BASIC, 
                    is_active=True,
                    ai_credits_balance=1500,
                    current_period_end=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30) # 30-day trial for new users                 
                    
                )


                user_prefs = UserPreferences(
                    user_id=user.id,
                )

                # 3. Persistence (Staged in the session)
                await self.uow.user_repo.save(user)
                await self.uow.auth_repo.save(auth_user)
                await self.uow.subscription_repo.save(sub_user)
                await self.uow.user_pref_repo.save(user_prefs)

                await self.uow.commit()
                
               

            return Result.success(UserResponse.from_entity(user))

        except ValidationError as e:
            return Result.failure(Error.validation_error(str(e)))
        except Exception as e:
            return Result.failure(Error.system_error(str(e)))



@dataclass
class LoginUserUseCase:

    password_service: PasswordServicePort
    token_provider: TokenProviderPort
    uow: UnitOfWork

    async def execute(self, request: LoginRequest) -> Result:
        try:
            params = request.to_execution_params()

            async with self.uow:
                # 1. Fetch User Credentials
                auth_user = await self.uow.auth_repo.get_by_email(params["email"])
                
                # 2. Verify Existence and Password
                # Note: We return "Invalid credentials" for both cases to avoid leaking user existence
                if not auth_user:
                    return Result.failure(Error.not_found("User"))

                if not self.password_service.verify(params["password"], auth_user.password_hash):
                    return Result.failure(Error.unauthorized("Invalid credentials"))

                # 3. Domain Logic: Record Login (Optional)
                auth_user.record_login()
                await self.uow.auth_repo.save(auth_user)

                # 4. Generate Token
                token = self.token_provider.encode_token(
                    user_id=auth_user.user_id, 
                    claims={"email": auth_user.email}
                )

            # 5. Response
            return Result.success(LoginResponse(access_token=token, token_type="Bearer"))

        except ValueError as e:
            return Result.failure(Error.validation_error(str(e)))
            
        except Exception as e:
            return Result.failure(Error.system_error(str(e)))
        



@dataclass
class LogoutUseCase:    
        
    token_provider: TokenProviderPort
    token_blacklist_repo: TokenBlacklistRepository


    async def execute(self, token: str) -> None:
        """
        Invalidates the given token so it cannot be used again.
        """
        try:
            
            # 1. Extract the unique ID (JTI)
            jti = self.token_provider.get_token_id(token)
            
            # 2. Calculate how much time is left on the token
            ttl = self.token_provider.get_token_ttl(token)

            # 3. If the token is valid and has time remaining, blacklist it.
            #    If ttl is 0, it's already expired, so no need to blacklist.
            if jti and ttl > 0:
                self.token_blacklist_repo.blacklist_token(token_id=jti, ttl_seconds=ttl)
                
        except InvalidTokenException:
            # If the token is already invalid (malformed, expired, fake), 
            # logging out is technically a "success" (the user session is dead).
            # We fail silently here.
            pass





@dataclass
class ChangePasswordUseCase:
    
    password_service: PasswordServicePort
    uow: UnitOfWork

    async def execute(self, request: ChangePasswordRequest) -> Result:
        try:
            params = request.to_execution_params()
            async with self.uow as uow:
                # 1. Fetch User by ID (from Token)
                auth_user = await uow.auth_repo.get_by_id(params["user_id"])
                
                if not auth_user:
                    return Result.failure(Error.not_found("User", str(params["user_id"])))

                # 2. Security Check: Verify OLD Password
                # This ensures the user actually owns the account
                if not self.password_service.verify(params["old_password"], auth_user.password_hash):
                    return Result.failure(Error.unauthorized("Invalid old password"))

                # 3. Hash the NEW Password
                new_hashed_password = self.password_service.get_password_hash(params["new_password"])

                # 4. Domain Logic: Update Entity
                # This handles setting the new hash and updating 'updated_at'
                auth_user.change_password(new_hashed_password)

                # 5. Persist Changes
                await uow.auth_repo.save(auth_user)

            # 6. Response
            return Result.success("Password changed successfully")

        except ValidationError as e:
            return Result.failure(Error.validation_error(str(e)))
            
        except Exception as e:
            # Catch-all for database errors or unexpected issues
            return Result.failure(Error.system_error(str(e)))


@dataclass
class GetUserUseCase:

    uow: UnitOfWork

    async def execute(self, request: GetUserRequest) -> Result:
        """
        Execute the use case.

        Args:
        request: a GetUserRequest object containing the unique identifier of the user 

        Returns Result containing:
        - UserResponse if successful
        - Error information

        """
        try:
            params = request.to_execution_params()
            async with self.uow as uow:                
                user = await uow.user_repo.get(params["user_id"])      
            return Result.success(UserResponse.from_entity(user))

        except UserNotFoundError:
            return Result.failure(Error.not_found("User", str(params["user_id"])))


@dataclass
class UpdateUserUseCase:

  uow: UnitOfWork


  async def execute(self, request: UpdateUserRequest) -> Result:
    try:
        async with self.uow as uow:
            params = request.to_execution_params() 
            user_id = params.pop("user_id")                  
            user = await uow.user_repo.update(user_id, params)
        return Result.success(UserResponse.from_entity(user))

    except UserNotFoundError:
        return Result.failure(Error.not_found("User", str(user_id)))
    except ValidationError as e:
        return Result.failure(Error.validation_error(str(e)))
    except BusinessRuleViolation as e:
        return Result.failure(Error.business_rule_violation(str(e)))



@dataclass
class DeleteUserUseCase:
  """Use case for deleting a user"""
  uow: UnitOfWork


  async def execute(self, request: GetUserRequest) -> Result:
    """
    Execute the use case.

      Args:
        request: a GetUserRequest object that contains the unique identifier of the user to delete

      Returns:
        Result containing DeletionResult if successful

    """
    
    try:
      async with self.uow as uow:
        params = request.to_execution_params()
        await uow.user_repo.delete(params["user_id"])
      return Result.success(DeletionOutcome(params["user_id"]))
    
    except UserNotFoundError:
      return Result.failure(Error.not_found("User", str(params["user_id"])))








    

         
