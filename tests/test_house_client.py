import io
import zipfile
from datetime import date

import httpx
import respx

from polytrader.congress.house_client import (
    HOUSE_CLERK_BASE_URL,
    HouseClerkClient,
    parse_filing_index_xml,
)

SAMPLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<FinancialDisclosure>
  <Member>
    <Last>Pelosi</Last>
    <First>Nancy</First>
    <StateDst>CA11</StateDst>
    <Year>2026</Year>
    <FilingType>P</FilingType>
    <FilingDate>08/15/2026</FilingDate>
    <DocID>20026001</DocID>
  </Member>
  <Member>
    <Last>Smith</Last>
    <First>John</First>
    <StateDst>TX02</StateDst>
    <Year>2026</Year>
    <FilingType>O</FilingType>
    <FilingDate>08/16/2026</FilingDate>
    <DocID>20026002</DocID>
  </Member>
  <Member>
    <Last>NoDoc</Last>
    <First>Missing</First>
    <StateDst>NY01</StateDst>
    <Year>2026</Year>
    <FilingType>P</FilingType>
    <FilingDate>08/17/2026</FilingDate>
    <DocID></DocID>
  </Member>
</FinancialDisclosure>
"""


def _sample_zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("2026FD.xml", SAMPLE_XML)
    return buffer.getvalue()


def test_parse_filing_index_xml_extracts_entries():
    entries = parse_filing_index_xml(SAMPLE_XML, base_url=HOUSE_CLERK_BASE_URL)
    assert len(entries) == 2  # the entry with no DocID is skipped

    pelosi = entries[0]
    assert pelosi.first_name == "Nancy"
    assert pelosi.last_name == "Pelosi"
    assert pelosi.state_district == "CA11"
    assert pelosi.filing_date == date(2026, 8, 15)
    assert pelosi.doc_id == "20026001"
    assert pelosi.pdf_url == f"{HOUSE_CLERK_BASE_URL}/public_disc/financial-pdfs/20026001.pdf"


def test_parse_filing_index_xml_skips_entries_without_doc_id():
    entries = parse_filing_index_xml(SAMPLE_XML)
    assert all(e.doc_id for e in entries)
    assert not any(e.last_name == "NoDoc" for e in entries)


@respx.mock
def test_fetch_filing_index_downloads_and_parses_zip():
    respx.get(f"{HOUSE_CLERK_BASE_URL}/public_disc/financial-pdfs/2026FD.zip").mock(
        return_value=httpx.Response(200, content=_sample_zip_bytes())
    )

    with HouseClerkClient() as client:
        entries = client.fetch_filing_index(2026)

    assert len(entries) == 2
    assert entries[1].last_name == "Smith"
