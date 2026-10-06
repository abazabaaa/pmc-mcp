"""Self-contained selections; time and signing key are explicit inputs."""

import base64
import binascii
import hashlib
import hmac

from pydantic import ValidationError

from pmc_mcp.domain.models import Artifact, Selection
from pmc_mcp.domain.rules import DomainError, validate_artifact

MAX_TOKEN = 16000


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def seal(artifact: Artifact, key: bytes, *, now: int, ttl: int = 86400) -> str:
    validate_artifact(artifact)
    if len(key) < 32 or ttl <= 0:
        raise DomainError("invalid_token_configuration")
    claims = Selection(artifact=artifact, issued_at=now, expires_at=now + ttl)
    payload = _encode(claims.model_dump_json().encode())
    signature = _encode(hmac.digest(key, payload.encode(), hashlib.sha256))
    token = f"{payload}.{signature}"
    if len(token) > MAX_TOKEN:
        raise DomainError("choice_too_large")
    return token


def unseal(token: str, key: bytes, *, now: int) -> Artifact:
    if len(key) < 32 or len(token) > MAX_TOKEN:
        raise DomainError("invalid_choice")
    try:
        payload, signature = token.split(".")
        expected = hmac.digest(key, payload.encode(), hashlib.sha256)
        if not hmac.compare_digest(expected, _decode(signature)):
            raise DomainError("invalid_choice")
        claims = Selection.model_validate_json(_decode(payload))
        validate_artifact(claims.artifact)
    except (ValueError, UnicodeError, binascii.Error, ValidationError) as error:
        raise DomainError("invalid_choice") from error
    if claims.issued_at > now or claims.expires_at <= now or claims.expires_at <= claims.issued_at:
        raise DomainError("choice_expired")
    return claims.artifact
