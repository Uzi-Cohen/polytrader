"""Client for the Federal Register API (free, no key): executive orders,
agency rules/notices -- the "executive actions" and "regulatory decisions"
event types from the merged product thesis.

Not live-tested from inside this build's sandbox: federalregister.gov is
blocked by org egress policy (confirmed 403 at the proxy's CONNECT layer).
The shape below matches the Federal Register's long-stable v1
`documents.json` endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import httpx

FEDERAL_REGISTER_BASE_URL = "https://www.federalregister.gov"


@dataclass(frozen=True)
class RegisterDocument:
    document_number: str
    title: str
    document_type: str
    publication_date: date | None
    agencies: list[str]
    abstract: str | None
    html_url: str
    raw: dict


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_document(raw: dict) -> RegisterDocument:
    return RegisterDocument(
        document_number=str(raw.get("document_number", "")),
        title=raw.get("title") or "(untitled document)",
        document_type=raw.get("type") or "Unknown",
        publication_date=_parse_date(raw.get("publication_date")),
        agencies=[a.get("name", "") for a in (raw.get("agencies") or [])],
        abstract=raw.get("abstract"),
        html_url=raw.get("html_url") or "",
        raw=raw,
    )


class FederalRegisterClient:
    def __init__(self, base_url: str = FEDERAL_REGISTER_BASE_URL, timeout: float = 30.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "FederalRegisterClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def search_documents(
        self,
        *,
        term: str | None = None,
        document_types: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        per_page: int = 50,
        page: int = 1,
    ) -> list[RegisterDocument]:
        params: dict = {
            "per_page": per_page,
            "page": page,
            "order": "newest",
            "fields[]": [
                "document_number",
                "title",
                "type",
                "publication_date",
                "agencies",
                "abstract",
                "html_url",
            ],
        }
        if term:
            params["conditions[term]"] = term
        if document_types:
            params["conditions[type][]"] = document_types
        if start_date:
            params["conditions[publication_date][gte]"] = start_date.isoformat()
        if end_date:
            params["conditions[publication_date][lte]"] = end_date.isoformat()

        response = self._client.get("/api/v1/documents.json", params=params)
        response.raise_for_status()
        payload = response.json()
        return [_parse_document(r) for r in payload.get("results", [])]
