from polytrader.contracts.federal_register_client import _parse_document

RAW_DOCUMENT = {
    "document_number": "2026-12345",
    "title": "Executive Order on Federal AI Procurement",
    "type": "Presidential Document",
    "publication_date": "2026-08-01",
    "agencies": [{"name": "Executive Office of the President"}],
    "abstract": "Directs agencies to accelerate AI adoption.",
    "html_url": "https://www.federalregister.gov/documents/2026/08/01/2026-12345/example",
}


def test_parses_document_fields():
    doc = _parse_document(RAW_DOCUMENT)
    assert doc.document_number == "2026-12345"
    assert doc.document_type == "Presidential Document"
    assert doc.publication_date.isoformat() == "2026-08-01"
    assert doc.agencies == ["Executive Office of the President"]
    assert doc.html_url.endswith("/example")


def test_handles_missing_optional_fields():
    minimal = {"document_number": "2026-1"}
    doc = _parse_document(minimal)
    assert doc.title == "(untitled document)"
    assert doc.document_type == "Unknown"
    assert doc.publication_date is None
    assert doc.agencies == []
