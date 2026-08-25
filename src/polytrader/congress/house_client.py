"""Client for House of Representatives financial-disclosure filing
metadata -- index-level only. See docs/DATA_SOURCES.md for why: the House
Clerk (disclosures-clerk.house.gov) publishes STOCK Act periodic
transaction reports as PDFs, not structured data, and the community feed
that used to fill this gap (House Stock Watcher) is dead (confirmed 403s,
unmaintained since mid-2025).

This connector targets the House Clerk's documented annual bulk-data
format: a ZIP per year (`{year}FD.zip`) containing an XML index
(`{year}FD.xml`) of every filer/filing/PDF for that year -- filer name,
filing type, filing date, and a document ID that maps to a PDF at
`{BASE}/public_disc/financial-pdfs/{doc_id}.pdf`. It does NOT extract
individual buy/sell transactions from those PDFs; that needs a PDF
(sometimes scanned-image) extraction pipeline that's out of scope here.

Not live-tested from inside this build's sandbox: disclosures-clerk.house.gov
is blocked by org egress policy (confirmed 403 at the proxy's CONNECT
layer). Unlike the USASpending/Federal Register/Gamma connectors, this
bulk-ZIP format could not be cross-checked against a live response during
development either -- verify the XML tag names below (`Member`, `Last`,
`First`, `FilingType`, `StateDst`, `FilingDate`, `DocID`) the first time
this runs somewhere that can reach the host, and adjust `_parse_entry`.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from xml.etree import ElementTree

import httpx

HOUSE_CLERK_BASE_URL = "https://disclosures-clerk.house.gov"
FILING_INDEX_URL_TEMPLATE = "{base}/public_disc/financial-pdfs/{year}FD.zip"
PDF_URL_TEMPLATE = "{base}/public_disc/financial-pdfs/{doc_id}.pdf"


@dataclass(frozen=True)
class HouseFilingIndexEntry:
    first_name: str
    last_name: str
    state_district: str | None
    filing_type: str
    filing_date: date | None
    doc_id: str
    pdf_url: str


def _parse_filing_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_entry(member_el: ElementTree.Element, *, base_url: str) -> HouseFilingIndexEntry | None:
    doc_id = (member_el.findtext("DocID") or "").strip()
    if not doc_id:
        return None
    return HouseFilingIndexEntry(
        first_name=(member_el.findtext("First") or "").strip(),
        last_name=(member_el.findtext("Last") or "").strip(),
        state_district=(member_el.findtext("StateDst") or "").strip() or None,
        filing_type=(member_el.findtext("FilingType") or "").strip() or "Unknown",
        filing_date=_parse_filing_date(member_el.findtext("FilingDate")),
        doc_id=doc_id,
        pdf_url=PDF_URL_TEMPLATE.format(base=base_url, doc_id=doc_id),
    )


def parse_filing_index_xml(xml_bytes: bytes, *, base_url: str = HOUSE_CLERK_BASE_URL) -> list[HouseFilingIndexEntry]:
    root = ElementTree.fromstring(xml_bytes)
    entries = []
    for member_el in root.findall(".//Member"):
        entry = _parse_entry(member_el, base_url=base_url)
        if entry:
            entries.append(entry)
    return entries


class HouseClerkClient:
    def __init__(self, base_url: str = HOUSE_CLERK_BASE_URL, timeout: float = 60.0) -> None:
        self._client = httpx.Client(timeout=timeout)
        self._base_url = base_url

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HouseClerkClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def fetch_filing_index(self, year: int) -> list[HouseFilingIndexEntry]:
        """Downloads and parses one year's bulk filing-index ZIP. Real
        filer/filing/PDF-link metadata; no per-transaction data -- see the
        module docstring."""
        url = FILING_INDEX_URL_TEMPLATE.format(base=self._base_url, year=year)
        response = self._client.get(url)
        response.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            xml_names = [n for n in archive.namelist() if n.lower().endswith(".xml")]
            if not xml_names:
                raise ValueError(f"No XML index found in {url}")
            xml_bytes = archive.read(xml_names[0])

        return parse_filing_index_xml(xml_bytes, base_url=self._base_url)
