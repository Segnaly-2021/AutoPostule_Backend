from uuid import UUID, uuid4
from typing import Dict, Any, Optional
from datetime import datetime, timedelta, timezone
import jwt
import os


# Import your Port and the custom Exception
from auto_apply_app.application.service_ports.token_provider_port import TokenProviderPort
from auto_apply_app.domain.exceptions import InvalidTokenException

class JwtTokenProvider(TokenProviderPort):
    def __init__(self):
        # Best Practice: Never hardcode secrets. Read from Environment.
        self.secret = os.getenv("JWT_SECRET", "BoulSakhKhallateSecretLaaa")
        self.algo = "HS256"
        self.token_lifespan_minutes = 120

    def encode_token(self, user_id: UUID, claims: Optional[Dict[str, Any]] = None) -> str:
        # 1. Prepare base payload
        payload = {
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=self.token_lifespan_minutes),
            "sub": str(user_id),  # CRITICAL: Convert UUID to string
            "jti": str(uuid4())
        }

        # 2. Add extra claims (like email) if provided
        if claims:
            payload.update(claims)

        # 3. Encode
        return jwt.encode(payload, self.secret, algorithm=self.algo)
  
    def decode_token(self, token: str) -> Dict[str, Any]:
        try:
            payload = jwt.decode(
                token,
                key=self.secret,
                algorithms=[self.algo] # PyJWT expects a list for algorithms
            )
            return payload
            
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError) as e:
            # Wrap 3rd party library errors in our own Domain Exception
            # DO NOT raise HTTPException here.
            raise InvalidTokenException(str(e))
        

    # <--- CHANGE 3: Implement the new helper methods
    def get_token_id(self, token: str) -> str:
        payload = self.decode_token(token)
        return payload.get("jti")

    def get_token_ttl(self, token: str) -> int:
        payload = self.decode_token(token)
        exp_timestamp = payload.get("exp")
        
        if not exp_timestamp:
            return 0

        # Calculate remaining seconds
        expiration_time = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        remaining = int((expiration_time - now).total_seconds())
        
        return max(remaining, 0)