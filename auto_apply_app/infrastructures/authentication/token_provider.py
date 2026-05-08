from uuid import UUID, uuid4
from typing import Dict, Any, Optional
from datetime import datetime, timedelta, timezone
import jwt
import os
import logging

from auto_apply_app.application.service_ports.token_provider_port import TokenProviderPort
from auto_apply_app.domain.exceptions import InvalidTokenException


logger = logging.getLogger(__name__)


class JwtTokenProvider(TokenProviderPort):
    def __init__(self):
        self.secret = os.getenv("JWT_SECRET")
        if not self.secret:
            raise ValueError("JWT_SECRET environment variable is not set")
        if len(self.secret) < 32:
            raise ValueError(f"JWT_SECRET must be at least 32 bytes (current: {len(self.secret)} bytes)")
        self.algo = "HS256"
        self.token_lifespan_minutes = 60

    def encode_token(self, user_id: UUID, claims: Optional[Dict[str, Any]] = None, expires_delta: Optional[timedelta] = None) -> str:
        # Determine the expiration time dynamically
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=self.token_lifespan_minutes)

        # 1. Prepare base payload
        payload = {
            "iss": "autopostule-api",
            "aud": "autopostule-frontend",
            "iat": datetime.now(timezone.utc),
            "exp": expire,
            "sub": str(user_id),
            "jti": str(uuid4())
        }

        # 2. Add extra claims (like email or purpose) if provided
        if claims:
            payload.update(claims)

        # 3. Encode
        return jwt.encode(payload, self.secret, algorithm=self.algo)
  
    def decode_token(self, token: str) -> Dict[str, Any]:
        try:
            payload = jwt.decode(
                token,
                key=self.secret,
                algorithms=[self.algo],
                issuer="autopostule-api",
                audience="autopostule-frontend",
                leeway=10  # Allow 10 seconds of clock skew
            )
            return payload
            
        except jwt.ExpiredSignatureError:
            logger.info("Token expired")
            raise InvalidTokenException("Token expired or invalid")
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            raise InvalidTokenException("Token expired or invalid")
                
    def get_token_id(self, token: str) -> str:
        payload = self.decode_token(token)
        return payload.get("jti")

    def get_token_ttl(self, token: str) -> int:
        payload = self.decode_token(token)
        exp_timestamp = payload.get("exp")
        
        if not exp_timestamp:
            return 0

        expiration_time = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        remaining = int((expiration_time - now).total_seconds())
        
        return max(remaining, 0)