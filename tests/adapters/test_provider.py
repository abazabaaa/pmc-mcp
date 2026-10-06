import httpx2
import pytest

from pmc_mcp.adapters.http import Reader
from pmc_mcp.adapters.pmc import BUCKET, PMC, parse_artifact
from pmc_mcp.domain.rules import DomainError, normalize_query


def metadata(**changes):
    return {
        "pmcid": "PMC123",
        "version": 2,
        "title": "Synthetic article",
        "doi": "10.1234/example",
        "license_code": "CC BY",
        "is_manuscript": "no",
        "is_retracted": "yes",
        "pdf_url": "s3://pmc-oa-opendata/PMC123.2/PMC123.2.pdf?md5=" + "a" * 32,
        **changes,
    }


def test_metadata_retains_flags_and_rejects_a_supplement():
    result = parse_artifact(metadata(), "PMC123", 2, "pdf")
    assert result is not None and result.retracted is True and result.manuscript is False
    with pytest.raises(DomainError, match="artifact_identity_mismatch"):
        parse_artifact(
            metadata(pdf_url="s3://pmc-oa-opendata/PMC123.2/mmc1.pdf?md5=" + "a" * 32),
            "PMC123",
            2,
            "pdf",
        )


async def test_explicit_version_and_converter_identity():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "pmc.ncbi.nlm.nih.gov":
            return httpx2.Response(
                200,
                json={
                    "records": [
                        {
                            "requested-id": "PMC123",
                            "pmcid": "PMC123",
                            "versions": [{"pmcid": "PMC123.2", "current": True}],
                        }
                    ]
                },
            )
        return httpx2.Response(200, json=metadata())

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        provider = PMC(Reader(client, ncbi_spacing=0))
        page = await provider.search(normalize_query("PMC123.2"), ("pdf",), 5, 0)
        assert len(page.artifacts) == 1 and page.artifacts[0].current is True
    assert seen[-1].endswith("/metadata/PMC123.2.json")
    assert not any("list-type" in url for url in seen)


async def test_version_gap_and_missing_pdf_are_distinct():
    def handler(request):
        if request.url.host == "eutils.ncbi.nlm.nih.gov":
            return httpx2.Response(200, json={"esearchresult": {"idlist": ["123"]}})
        if "list-type" in request.url.query.decode():
            return httpx2.Response(
                200,
                content=b"<ListBucketResult><IsTruncated>false</IsTruncated><Contents><Key>metadata/PMC123.2.json</Key></Contents></ListBucketResult>",
            )
        return httpx2.Response(200, json=metadata(pdf_url=None))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        page = await PMC(Reader(client, ncbi_spacing=0)).search(
            normalize_query("Synthetic title"), ("pdf",), 5, 0
        )
    assert page.artifacts == ()
    assert page.notices[0].code == "artifact_unavailable"


async def test_wrong_doi_converter_mapping_is_rejected():
    def handler(request):
        return httpx2.Response(
            200,
            json={
                "records": [
                    {"requested-id": "10.1234/example", "doi": "10.9999/wrong", "pmcid": "PMC123"}
                ]
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(DomainError, match="artifact_identity_mismatch"):
            await PMC(Reader(client, ncbi_spacing=0)).search(
                normalize_query("10.1234/example"), ("pdf",), 5, 0
            )


@pytest.mark.parametrize(
    "status,headers,body,code",
    [
        (302, {"location": "https://localhost/secret"}, b"", "blocked_redirect"),
        (429, {"retry-after": "90"}, b"", "rate_limited"),
        (404, {}, b"", "source_missing"),
        (200, {}, b"abcde", "too_large"),
    ],
)
async def test_http_rejects_redirects_long_backoff_missing_and_oversize(
    status, headers, body, code
):
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx2.Response(status, headers=headers, content=body)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        reader = Reader(client, metadata_cap=4, budget=1)
        with pytest.raises(DomainError, match=code):
            await reader.get(BUCKET + "/metadata/PMC123.2.json")
    assert len(seen) == 1


async def test_retry_before_stream_but_never_restart_partial_bytes():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx2.Response(503, headers={"retry-after": "0"})
        return httpx2.Response(200, content=b"ok")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        assert await Reader(client).get(BUCKET + "/metadata/PMC123.2.json") == b"ok"
    assert calls == 2

    class BrokenStream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield b"first bytes"
            raise httpx2.ReadError("synthetic disconnect")

    calls = 0

    def broken(request):
        nonlocal calls
        calls += 1
        return httpx2.Response(200, stream=BrokenStream())

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(broken)) as client:
        with pytest.raises(DomainError, match="source_unavailable"):
            await Reader(client).get(BUCKET + "/metadata/PMC123.2.json")
    assert calls == 1


async def test_malformed_converter_versions_is_a_structured_failure():
    async def handle(request):
        return httpx2.Response(
            200, json={"records": [{"requested-id": "PMC123", "pmcid": "PMC123", "versions": None}]}
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        provider = PMC(Reader(client, ncbi_spacing=0))
        with pytest.raises(DomainError, match="invalid_source_metadata"):
            await provider.search(normalize_query("PMC123"), ("pdf",), 5, 0)


async def test_entity_expansion_in_bucket_listing_is_rejected():
    async def handle(request):
        return httpx2.Response(200, content=b'<!DOCTYPE x [<!ENTITY x "expansion">]><x>&x;</x>')

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        provider = PMC(Reader(client, ncbi_spacing=0))
        with pytest.raises(DomainError, match="invalid_source_metadata"):
            await provider._versions("PMC123")
