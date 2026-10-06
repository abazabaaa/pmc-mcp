"""Immutable values crossing the domain and effect boundaries."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ArtifactKind = Literal["pdf", "jats"]


class Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Query(Value):
    kind: Literal["pmcid", "pmid", "doi", "text"]
    value: str
    version: int | None = None


class Artifact(Value):
    pmcid: str = Field(pattern=r"^PMC[1-9]\d*$")
    version: int = Field(gt=0, strict=True)
    kind: ArtifactKind
    key: str
    md5: str = Field(pattern=r"^[a-f0-9]{32}$")
    title: str = Field(min_length=1, max_length=2000)
    doi: str | None = None
    pmid: str | None = None
    authors: tuple[str, ...] = ()
    year: str | None = None
    citation: str | None = None
    license_code: str | None = None
    manuscript: bool | None = None
    retracted: bool | None = None
    current: bool | None = None

    @property
    def expression(self) -> str:
        return f"{self.pmcid}.{self.version}"

    @property
    def filename(self) -> str:
        suffix = "pdf" if self.kind == "pdf" else "xml"
        return f"{self.expression}-main.{suffix}"


class Selection(Value):
    schema_version: Literal[1] = 1
    artifact: Artifact
    issued_at: int = Field(ge=0, strict=True)
    expires_at: int = Field(ge=0, strict=True)


class Choice(Value):
    artifact: Artifact
    selection_token: str
    availability: Literal["advertised"] = "advertised"


class Notice(Value):
    code: str
    pmcid: str | None = None


class CatalogPage(Value):
    artifacts: tuple[Artifact, ...] = ()
    notices: tuple[Notice, ...] = ()
    next_offset: int | None = None


class SearchResult(Value):
    status: Literal["ok", "no_matches", "unavailable", "invalid_query"]
    choices: tuple[Choice, ...] = ()
    notices: tuple[Notice, ...] = ()
    next_cursor: str | None = None


class Receipt(Value):
    status: Literal["downloaded", "already_present", "failed"]
    code: str | None = None
    artifact: Artifact | None = None
    path: str | None = None
    sha256: str | None = None
    size: int | None = None
    retrieved_at: str | None = None


class DownloadResult(Value):
    receipts: tuple[Receipt, ...]
