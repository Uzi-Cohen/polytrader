"""Entity resolution v1: deterministic normalization + a small alias table.

This is explicitly the "honest heuristic" PRD 2 §11 calls for at MVP --
not fuzzy matching or an ML model. It resolves the obvious cases
("DoD" / "Dept. of Defense" / "Department of Defense" -> one canonical
`GovernmentAgency` row) and otherwise creates a new canonical entity per
distinct normalized name, recording every raw variant seen as an alias so
a later, smarter resolver has real training signal instead of starting
from nothing.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from polytrader.core.models import GovernmentAgency, Organization

_ORG_SUFFIXES = re.compile(
    r"\b(INC|INCORPORATED|LLC|LLP|LP|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC)\b\.?",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[.,]")

# Known agency name variants -> canonical name. Extend as new variants show
# up in ingested data; this is intentionally small and explicit rather
# than a fuzzy-match model.
AGENCY_ALIASES: dict[str, str] = {
    "DOD": "Department of Defense",
    "DEPT OF DEFENSE": "Department of Defense",
    "DEPARTMENT OF DEFENSE": "Department of Defense",
    "US DEPARTMENT OF DEFENSE": "Department of Defense",
    "DHS": "Department of Homeland Security",
    "DEPT OF HOMELAND SECURITY": "Department of Homeland Security",
    "DEPARTMENT OF HOMELAND SECURITY": "Department of Homeland Security",
    "DOJ": "Department of Justice",
    "DEPT OF JUSTICE": "Department of Justice",
    "DEPARTMENT OF JUSTICE": "Department of Justice",
    "DOC": "Department of Commerce",
    "DEPT OF COMMERCE": "Department of Commerce",
    "DEPARTMENT OF COMMERCE": "Department of Commerce",
    "DOE": "Department of Energy",
    "DEPT OF ENERGY": "Department of Energy",
    "DEPARTMENT OF ENERGY": "Department of Energy",
    "VA": "Department of Veterans Affairs",
    "DEPT OF VETERANS AFFAIRS": "Department of Veterans Affairs",
    "DEPARTMENT OF VETERANS AFFAIRS": "Department of Veterans Affairs",
    "GSA": "General Services Administration",
    "NASA": "National Aeronautics and Space Administration",
    "NIH": "National Institutes of Health",
    "NSF": "National Science Foundation",
}


def _basic_normalize(name: str) -> str:
    cleaned = _PUNCTUATION.sub("", name.upper())
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned


def normalize_agency_name(raw_name: str) -> str:
    key = _basic_normalize(raw_name)
    return AGENCY_ALIASES.get(key, raw_name.strip())


def normalize_organization_name(raw_name: str) -> str:
    cleaned = _basic_normalize(raw_name)
    cleaned = _ORG_SUFFIXES.sub("", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned or raw_name.strip()


def _add_alias(existing: list[str], raw_name: str) -> list[str]:
    if raw_name not in existing:
        return [*existing, raw_name]
    return existing


def resolve_agency(session: Session, raw_name: str, *, jurisdiction: str = "US") -> GovernmentAgency:
    canonical_name = normalize_agency_name(raw_name)
    agency = session.scalars(
        select(GovernmentAgency).where(GovernmentAgency.canonical_name == canonical_name)
    ).first()
    if agency is None:
        agency = GovernmentAgency(
            canonical_name=canonical_name,
            aliases=json.dumps([raw_name] if raw_name != canonical_name else []),
            jurisdiction=jurisdiction,
        )
        session.add(agency)
        session.flush()
        return agency

    existing_aliases = json.loads(agency.aliases or "[]")
    if raw_name != canonical_name and raw_name not in existing_aliases:
        agency.aliases = json.dumps(_add_alias(existing_aliases, raw_name))
        session.flush()
    return agency


def resolve_organization(session: Session, raw_name: str, *, country: str = "US") -> Organization:
    canonical_name = normalize_organization_name(raw_name)
    org = session.scalars(select(Organization).where(Organization.canonical_name == canonical_name)).first()
    if org is None:
        org = Organization(
            canonical_name=canonical_name,
            aliases=json.dumps([raw_name] if raw_name != canonical_name else []),
            country=country,
            organization_type="vendor",
        )
        session.add(org)
        session.flush()
        return org

    existing_aliases = json.loads(org.aliases or "[]")
    if raw_name != canonical_name and raw_name not in existing_aliases:
        org.aliases = json.dumps(_add_alias(existing_aliases, raw_name))
        session.flush()
    return org
