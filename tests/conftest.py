import hashlib
import io

import pytest
from pypdf import PdfWriter

from pmc_mcp.domain.models import Artifact


@pytest.fixture
def pdf_body():
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


@pytest.fixture
def file_artifact(pdf_body):
    return Artifact(
        pmcid="PMC123",
        version=2,
        kind="pdf",
        key="PMC123.2/PMC123.2.pdf",
        md5=hashlib.md5(pdf_body).hexdigest(),
        title="Synthetic article",
        doi="10.1234/example",
        license_code="CC BY",
        retracted=True,
    )
