import pytest

from pmc_mcp.domain.models import Artifact
from pmc_mcp.domain.rules import (
    DomainError,
    check_content_url,
    check_unchanged,
    normalize_query,
    permits_local_copy,
    retry_delay,
    validate_artifact,
)
from pmc_mcp.domain.tokens import seal, unseal


@pytest.fixture
def artifact():
    return Artifact(
        pmcid="PMC123",
        version=2,
        kind="pdf",
        key="PMC123.2/PMC123.2.pdf",
        md5="a" * 32,
        title="A study",
        license_code="CC BY",
        manuscript=None,
        retracted=True,
    )


@pytest.mark.parametrize(
    "raw,kind,value,version",
    [
        (" pmc123.2 ", "pmcid", "PMC123", 2),
        ("123456", "pmid", "123456", None),
        ("https://doi.org/10.1234/Some%2FStudy", "doi", "10.1234/some/study", None),
        ("doi: 10.1234/Study", "doi", "10.1234/study", None),
        ("A study of choices", "text", "A study of choices", None),
    ],
)
def test_normalization(raw, kind, value, version):
    query = normalize_query(raw)
    assert (query.kind, query.value, query.version) == (kind, value, version)


@pytest.mark.parametrize(
    "raw", ["", "PMC123.0", "PMC123.2/other", "https://example.org/file", "x\x00y"]
)
def test_invalid_query_cannot_become_a_download_identifier(raw):
    with pytest.raises(DomainError):
        normalize_query(raw)


def test_selection_survives_serialization_and_keeps_notice(artifact):
    token = seal(artifact, b"synthetic-fixture-key-32-bytes-0000", now=100, ttl=20)
    restored = unseal(token, b"synthetic-fixture-key-32-bytes-0000", now=101)
    assert restored.version == 2
    assert restored.retracted is True
    assert restored.manuscript is None
    assert restored.md5 == "a" * 32


def test_signature_expiry_and_future_time_are_enforced(artifact):
    key = b"synthetic-fixture-key-32-bytes-0000"
    token = seal(artifact, key, now=100, ttl=20)
    for altered, other_key, now in [
        (token, b"x" * 32, 101),
        ("x" + token[1:], key, 101),
        (token, key, 120),
        (token, key, 99),
    ]:
        with pytest.raises(DomainError):
            unseal(altered, other_key, now=now)


def test_supplement_is_not_the_main_article(artifact):
    with pytest.raises(DomainError, match="artifact_identity_mismatch"):
        validate_artifact(artifact.model_copy(update={"key": "PMC123.2/mmc1.pdf"}))


@pytest.mark.parametrize(
    "change",
    [
        {"md5": "b" * 32},
        {"license_code": "TDM"},
        {"retracted": False},
        {"version": 3, "key": "PMC123.3/PMC123.3.pdf"},
    ],
)
def test_changed_metadata_requires_another_choice(artifact, change):
    with pytest.raises(DomainError, match="choice_changed"):
        check_unchanged(artifact, artifact.model_copy(update=change))


@pytest.mark.parametrize(
    "code,expected",
    [
        ("CC BY", True),
        ("CC-BY-NC-ND", True),
        ("CC0", True),
        ("TDM", False),
        (None, False),
        ("custom", False),
    ],
)
def test_rights_policy_has_explicit_unknowns(code, expected):
    assert permits_local_copy(code) is expected


@pytest.mark.parametrize(
    "url",
    [
        "http://pmc-oa-opendata.s3.amazonaws.com/PMC123.2/PMC123.2.pdf",
        "https://localhost/PMC123.2/PMC123.2.pdf",
        "https://pmc-oa-opendata.s3.amazonaws.com:444/PMC123.2/PMC123.2.pdf",
        "https://pmc-oa-opendata.s3.amazonaws.com/PMC123.2/mmc1.pdf",
        "https://pmc-oa-opendata.s3.amazonaws.com/PMC123.2/PMC123.2.pdf?token=secret",
    ],
)
def test_url_policy_rejects_unapproved_sources(url):
    with pytest.raises(DomainError):
        check_content_url(url)


def test_source_url_and_retry_deadlines():
    check_content_url("https://pmc-oa-opendata.s3.amazonaws.com/PMC123.2/PMC123.2.pdf")
    assert retry_delay(429, "90", attempt=0, now=0, remaining=30) is None
    assert retry_delay(429, "Thu, 01 Jan 1970 00:00:10 GMT", attempt=0, now=1, remaining=20) == 9
    assert retry_delay(403, None, attempt=0, now=0, remaining=20) is None
    assert retry_delay(503, None, attempt=1, now=0, remaining=20) == 2
