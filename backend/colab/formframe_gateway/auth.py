from __future__ import annotations

import hmac
from functools import lru_cache

from fastapi import Header, HTTPException

from .settings import GatewaySettings


class AccessVerifier:
    def __init__(self, settings: GatewaySettings) -> None:
        self.settings = settings

    @lru_cache(maxsize=1)
    def _jwk_client(self):
        try:
            import jwt
        except ImportError as exc:
            raise RuntimeError("PyJWT is required for Cloudflare Access validation") from exc
        domain = self.settings.access_team_domain
        if not domain.startswith("https://"):
            domain = f"https://{domain}"
        return jwt.PyJWKClient(f"{domain.rstrip('/')}/cdn-cgi/access/certs")

    def verify(self, assertion: str) -> dict[str, object]:
        try:
            import jwt
            signing_key = self._jwk_client().get_signing_key_from_jwt(assertion)
            domain = self.settings.access_team_domain
            if not domain.startswith("https://"):
                domain = f"https://{domain}"
            payload = jwt.decode(
                assertion,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.settings.access_audience,
                issuer=domain.rstrip("/"),
            )
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Cloudflare Access assertion is invalid") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=401, detail="Cloudflare Access assertion is invalid")
        return payload

    def dependency(
        self,
        cf_access_jwt_assertion: str = Header(default="", alias="Cf-Access-Jwt-Assertion"),
        authorization: str = Header(default=""),
    ) -> dict[str, object]:
        if self.settings.development_token:
            expected = f"Bearer {self.settings.development_token}"
            if hmac.compare_digest(authorization, expected):
                return {"mode": "development"}
            raise HTTPException(status_code=401, detail="Gateway development token is invalid")
        if not cf_access_jwt_assertion:
            raise HTTPException(status_code=401, detail="Cloudflare Access assertion is required")
        return self.verify(cf_access_jwt_assertion)

