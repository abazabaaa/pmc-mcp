"""PMC metadata and main-article artifact adapter."""

import json
import re
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from defusedxml import ElementTree
from pydantic import ValidationError

from pmc_mcp.adapters.http import Reader
from pmc_mcp.domain.models import Artifact, ArtifactKind, CatalogPage, Notice, Query
from pmc_mcp.domain.rules import (
    BUCKET_HOST,
    DomainError,
    check_content_url,
    permits_local_copy,
    source_key,
)

BUCKET = f"https://{BUCKET_HOST}"
CONVERTER = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"


def flag(value: object) -> bool | None:
    if value is True or value in ("yes", "true"):
        return True
    if value is False or value in ("no", "false"):
        return False
    return None


def parse_artifact(
    data: dict, pmcid: str, version: int, kind: ArtifactKind, *, current: bool | None = None
) -> Artifact | None:
    if data.get("pmcid") not in (pmcid, f"{pmcid}.{version}") or str(data.get("version")) != str(
        version
    ):
        raise DomainError("artifact_identity_mismatch")
    raw = data.get("pdf_url" if kind == "pdf" else "xml_url")
    if not raw:
        return None
    if not isinstance(raw, str):
        raise DomainError("invalid_source_metadata")
    parsed = urlsplit(raw)
    digest = parse_qs(parsed.query).get("md5", [])
    expected = source_key(pmcid, version, kind)
    if (
        parsed.scheme != "s3"
        or parsed.netloc != "pmc-oa-opendata"
        or parsed.path != f"/{expected}"
        or parsed.fragment
        or len(digest) != 1
    ):
        raise DomainError("artifact_identity_mismatch")
    title = data.get("title")
    if not isinstance(title, str):
        raise DomainError("invalid_source_metadata")
    try:
        result = Artifact(
            pmcid=pmcid,
            version=version,
            kind=kind,
            key=expected,
            md5=digest[0].lower(),
            title=title,
            doi=data.get("doi") or None,
            pmid=str(data["pmid"]) if data.get("pmid") else None,
            license_code=data.get("license_code"),
            manuscript=flag(data.get("is_manuscript")),
            retracted=flag(data.get("is_retracted")),
            current=current,
            citation=data.get("citation") if isinstance(data.get("citation"), str) else None,
        )
    except ValidationError as error:
        raise DomainError("invalid_source_metadata") from error
    return result


class PMC:
    def __init__(self, reader: Reader, *, contact_email: str | None = None):
        self.reader = reader
        self.contact_email = contact_email

    def params(self, **values: str | int) -> str:
        data: dict[str, str | int] = {"tool": "pmc-mcp", **values}
        if self.contact_email:
            data["email"] = self.contact_email
        return urlencode(data)

    async def _json(self, url: str) -> dict:
        try:
            result = json.loads(await self.reader.get(url))
        except (ValueError, UnicodeError) as error:
            raise DomainError("invalid_source_metadata") from error
        if not isinstance(result, dict):
            raise DomainError("invalid_source_metadata")
        return result

    async def _resolve(self, query: Query) -> tuple[str, dict[int, bool | None]]:
        data = await self._json(
            CONVERTER + "?" + self.params(ids=query.value, format="json", versions="yes")
        )
        records = data.get("records")
        if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
            raise DomainError("outside_initial_coverage")
        record = records[0]
        requested = str(record.get("requested-id", "")).lower()
        if requested != query.value.lower():
            raise DomainError("artifact_identity_mismatch")
        pmcid = str(record.get("pmcid", "")).split(".")[0].upper()
        if not re.fullmatch(r"PMC[1-9]\d*", pmcid):
            raise DomainError("outside_initial_coverage")
        if query.kind == "pmcid" and pmcid != query.value:
            raise DomainError("artifact_identity_mismatch")
        if query.kind == "doi" and str(record.get("doi", "")).lower() != query.value:
            raise DomainError("artifact_identity_mismatch")
        if query.kind == "pmid" and str(record.get("pmid", "")) != query.value:
            raise DomainError("artifact_identity_mismatch")
        versions: dict[int, bool | None] = {}
        for version in record.get("versions", []):
            if isinstance(version, dict):
                identifier = str(version.get("pmcid", ""))
                match = re.fullmatch(re.escape(pmcid) + r"\.([1-9]\d*)", identifier)
                if match:
                    versions[int(match[1])] = flag(version.get("current"))
        return pmcid, versions

    async def _versions(self, pmcid: str) -> list[int]:
        url = BUCKET + "/?" + urlencode({"list-type": "2", "prefix": f"metadata/{pmcid}."})
        raw = await self.reader.get(url)
        try:
            root = ElementTree.fromstring(raw)
        except (ValueError, ElementTree.ParseError) as error:
            raise DomainError("invalid_source_metadata") from error
        versions = []
        for node in root.iter():
            name = node.tag.rsplit("}", 1)[-1]
            if name == "IsTruncated" and node.text == "true":
                raise DomainError("catalog_truncated")
            if name == "Key":
                match = re.fullmatch(
                    r"metadata/" + re.escape(pmcid) + r"\.([1-9]\d*)\.json", node.text or ""
                )
                if match:
                    versions.append(int(match[1]))
        if len(versions) > 20:
            raise DomainError("catalog_truncated")
        return sorted(set(versions))

    async def _artifacts(
        self,
        pmcid: str,
        kinds: tuple[ArtifactKind, ...],
        version: int | None,
        currents: dict[int, bool | None],
    ) -> CatalogPage:
        versions = [version] if version is not None else await self._versions(pmcid)
        artifacts: list[Artifact] = []
        notices: list[Notice] = []
        for number in versions:
            data = await self._json(f"{BUCKET}/metadata/{pmcid}.{number}.json")
            for kind in kinds:
                artifact = parse_artifact(data, pmcid, number, kind, current=currents.get(number))
                if artifact is None:
                    notices.append(Notice(code="artifact_unavailable", pmcid=pmcid))
                elif not permits_local_copy(artifact.license_code):
                    notices.append(Notice(code="rights_not_configured", pmcid=pmcid))
                else:
                    artifacts.append(artifact)
        if not versions:
            notices.append(Notice(code="not_distributed", pmcid=pmcid))
        return CatalogPage(artifacts=tuple(artifacts), notices=tuple(notices))

    async def search(
        self, query: Query, kinds: tuple[ArtifactKind, ...], limit: int, offset: int
    ) -> CatalogPage:
        if query.kind != "text":
            if offset:
                return CatalogPage()
            pmcid, currents = await self._resolve(query)
            page = await self._artifacts(pmcid, kinds, query.version, currents)
            for artifact in page.artifacts:
                if query.kind == "doi" and (artifact.doi or "").lower() != query.value:
                    raise DomainError("artifact_identity_mismatch")
                if query.kind == "pmid" and artifact.pmid != query.value:
                    raise DomainError("artifact_identity_mismatch")
            return page
        data = await self._json(
            ESEARCH
            + "?"
            + self.params(
                db="pmc",
                term=query.value,
                retmode="json",
                retmax=limit,
                retstart=offset,
                sort="relevance",
            )
        )
        result = data.get("esearchresult")
        if not isinstance(result, dict) or not isinstance(result.get("idlist"), list):
            raise DomainError("invalid_source_metadata")
        artifacts: list[Artifact] = []
        notices: list[Notice] = []
        for identifier in result["idlist"][:limit]:
            if not re.fullmatch(r"[1-9]\d*", str(identifier)):
                raise DomainError("artifact_identity_mismatch")
            pmcid = f"PMC{identifier}"
            try:
                page = await self._artifacts(pmcid, kinds, None, {})
                artifacts.extend(page.artifacts)
                notices.extend(page.notices)
            except DomainError as error:
                notices.append(Notice(code=error.code, pmcid=pmcid))
        next_offset = offset + len(result["idlist"]) if len(result["idlist"]) == limit else None
        return CatalogPage(
            artifacts=tuple(artifacts), notices=tuple(notices), next_offset=next_offset
        )

    async def refresh(self, artifact: Artifact) -> Artifact:
        data = await self._json(f"{BUCKET}/metadata/{artifact.expression}.json")
        fresh = parse_artifact(
            data, artifact.pmcid, artifact.version, artifact.kind, current=artifact.current
        )
        if fresh is None:
            raise DomainError("source_missing")
        return fresh

    def content(self, artifact: Artifact):
        url = f"{BUCKET}/{quote(artifact.key, safe='/')}"
        check_content_url(url)
        return self.reader.stream(url, cap=50 * 2**20)
