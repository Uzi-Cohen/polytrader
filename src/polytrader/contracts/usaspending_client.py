"""Client for USASpending.gov's award search API (free, no key).

Covers "general US contracts" across every federal agency -- broader than
PRD 2's original single-jurisdiction pilot scope, per the user's request.

Not live-tested from inside this build's sandbox: api.usaspending.gov is
blocked by org egress policy (confirmed 403 at the proxy's CONNECT layer).
The request/response shape below matches USASpending's long-stable v2
`spending_by_award` contract; verify against a live response the first
time this runs somewhere that can reach the host.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import httpx

USASPENDING_BASE_URL = "https://api.usaspending.gov"

# Procurement award type codes: contracts (not grants/loans/other assistance).
CONTRACT_AWARD_TYPE_CODES = ["A", "B", "C", "D"]

_FIELDS = [
    "Award ID",
    "Recipient Name",
    "Awarding Agency",
    "Awarding Sub Agency",
    "Award Amount",
    "Start Date",
    "End Date",
    "Description",
    "generated_internal_id",
    "Contract Award Type",
]


@dataclass(frozen=True)
class ContractAward:
    award_id: str
    generated_internal_id: str
    title: str
    recipient_name: str
    awarding_agency: str
    awarding_sub_agency: str | None
    amount: float | None
    start_date: date | None
    end_date: date | None
    permalink: str
    raw: dict


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_award(raw: dict) -> ContractAward:
    generated_id = raw.get("generated_internal_id", "")
    return ContractAward(
        award_id=str(raw.get("Award ID") or generated_id),
        generated_internal_id=str(generated_id),
        title=raw.get("Description") or raw.get("Award ID") or "(untitled award)",
        recipient_name=raw.get("Recipient Name") or "Unknown recipient",
        awarding_agency=raw.get("Awarding Agency") or "Unknown agency",
        awarding_sub_agency=raw.get("Awarding Sub Agency"),
        amount=float(raw["Award Amount"]) if raw.get("Award Amount") is not None else None,
        start_date=_parse_date(raw.get("Start Date")),
        end_date=_parse_date(raw.get("End Date")),
        permalink=f"https://www.usaspending.gov/award/{generated_id}" if generated_id else "",
        raw=raw,
    )


class USASpendingClient:
    def __init__(self, base_url: str = USASPENDING_BASE_URL, timeout: float = 30.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "USASpendingClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def search_awards(
        self,
        *,
        keywords: list[str] | None = None,
        agency_name: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 50,
        page: int = 1,
    ) -> list[ContractAward]:
        filters: dict = {"award_type_codes": CONTRACT_AWARD_TYPE_CODES}
        if keywords:
            filters["keywords"] = keywords
        if agency_name:
            filters["agencies"] = [{"type": "awarding", "tier": "toptier", "name": agency_name}]
        if start_date or end_date:
            filters["time_period"] = [
                {
                    "start_date": (start_date or date(2020, 1, 1)).isoformat(),
                    "end_date": (end_date or date.today()).isoformat(),
                }
            ]

        body = {
            "filters": filters,
            "fields": _FIELDS,
            "page": page,
            "limit": limit,
            "sort": "Award Amount",
            "order": "desc",
        }
        response = self._client.post("/api/v2/search/spending_by_award/", json=body)
        response.raise_for_status()
        payload = response.json()
        return [_parse_award(r) for r in payload.get("results", [])]
