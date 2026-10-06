"""Pure identifier, source, rights, fingerprint, and retry rules."""

import hashlib
import json
import math
import re
from email.utils import parsedate_to_datetime
from urllib.parse import unquote, urlsplit

from pmc_mcp.domain.models import Artifact, ArtifactKind, Query

BUCKET_HOST = "pmc-oa-opendata.s3.amazonaws.com"


class DomainError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def normalize_query(value: str) -> Query:
    value = value.strip()
    if not value or len(value) > 2000 or any(ord(c) < 32 for c in value):
        raise DomainError("invalid_query")
    match = re.fullmatch(r"(?i)PMC([1-9]\d*)(?:\.([1-9]\d*))?", value)
    if match:
        return Query(
            kind="pmcid", value=f"PMC{match[1]}", version=int(match[2]) if match[2] else None
        )
    if re.fullmatch(r"[1-9]\d{0,9}", value):
        return Query(kind="pmid", value=value)
    if value.lower().startswith(("https://doi.org/", "http://doi.org/")):
        value = unquote(urlsplit(value).path.lstrip("/"))
    value = re.sub(r"(?i)^doi:\s*", "", value)
    if re.fullmatch(r"10\.\d{4,9}/\S+", value):
        return Query(kind="doi", value=value.lower())
    if re.match(r"(?i)^(PMC\d|10\.|https?://|doi:)", value):
        raise DomainError("invalid_identifier")
    return Query(kind="text", value=value)


def source_key(pmcid: str, version: int, kind: ArtifactKind) -> str:
    expression = f"{pmcid}.{version}"
    return f"{expression}/{expression}.{'pdf' if kind == 'pdf' else 'xml'}"


def validate_artifact(artifact: Artifact) -> None:
    if artifact.key != source_key(artifact.pmcid, artifact.version, artifact.kind):
        raise DomainError("artifact_identity_mismatch")


def fingerprint(artifact: Artifact) -> str:
    validate_artifact(artifact)
    body = json.dumps(artifact.model_dump(), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def check_unchanged(selected: Artifact, fresh: Artifact) -> None:
    if fingerprint(selected) != fingerprint(fresh):
        raise DomainError("choice_changed")


def permits_local_copy(license_code: str | None) -> bool:
    code = re.sub(r"[^A-Z0-9]", "", (license_code or "").upper())
    return code in {"CC0", "CCBY", "CCBYSA", "CCBYNC", "CCBYNCSA", "CCBYND", "CCBYNCND"}


def check_content_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != BUCKET_HOST
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.query
            or not re.fullmatch(
                r"/PMC[1-9]\d*\.[1-9]\d*/PMC[1-9]\d*\.[1-9]\d*\.(pdf|xml)", parsed.path
            )
        ):
            raise DomainError("blocked_source_url")
    except ValueError as error:
        raise DomainError("blocked_source_url") from error


def retry_delay(
    status: int, retry_after: str | None, *, attempt: int, now: float, remaining: float
) -> float | None:
    if status not in {429, 500, 502, 503, 504} or attempt >= 2:
        return None
    delay = float(2**attempt)
    if retry_after:
        try:
            delay = float(retry_after)
        except ValueError:
            try:
                date = parsedate_to_datetime(retry_after)
                if date.tzinfo is None:
                    return None
                delay = max(0.0, date.timestamp() - now)
            except (TypeError, ValueError, OverflowError):
                return None
    if not math.isfinite(delay) or delay < 0 or delay >= remaining:
        return None
    return delay
