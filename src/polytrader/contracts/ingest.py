"""Orchestrates USASpending + Federal Register ingestion into the
canonical contract/event store -- a condensed version of PRD 2 section
10's pipeline (collector -> raw storage -> entity resolution -> canonical
database) sized for this build. Every ingested record gets a
SourceDocument with a real URL and ingestion timestamp (PRD 2's
provenance requirement), and publishes a DomainEvent so signals/matcher.py
can ask whether it moves any open Polymarket market.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from polytrader.contracts.entity_resolution import resolve_agency, resolve_organization
from polytrader.contracts.federal_register_client import RegisterDocument
from polytrader.contracts.usaspending_client import ContractAward
from polytrader.core.events import DomainEvent, get_event_bus
from polytrader.core.models import Contract, ContractEvent, SourceDocument


def _as_datetime(d: date | None) -> datetime | None:
    if d is None:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def ingest_contract_award(session: Session, award: ContractAward) -> Contract:
    source = SourceDocument(
        url=award.permalink or f"usaspending:{award.award_id}",
        source_name="usaspending.gov",
        document_type="contract_award",
        ingestion_timestamp=datetime.now(timezone.utc),
    )
    session.add(source)
    session.flush()

    agency = resolve_agency(session, award.awarding_agency)
    vendor = resolve_organization(session, award.recipient_name)

    contract_id = award.generated_internal_id or award.award_id
    contract = session.get(Contract, contract_id)
    is_new = contract is None
    if contract is None:
        contract = Contract(contract_id=contract_id)
        session.add(contract)

    contract.title = award.title
    contract.awarding_agency_id = agency.agency_id
    contract.vendor_org_id = vendor.org_id
    contract.award_date = _as_datetime(award.start_date)
    contract.start_date = _as_datetime(award.start_date)
    contract.end_date = _as_datetime(award.end_date)
    contract.status = "active"
    contract.value = award.amount
    contract.value_type = "disclosed_award"
    contract.jurisdiction = "US"
    contract.source_document_id = source.document_id
    session.flush()

    session.add(
        ContractEvent(
            contract_id=contract.contract_id,
            event_type="award" if is_new else "update",
            event_date=_as_datetime(award.start_date),
            value_delta=award.amount if is_new else None,
            source_document_id=source.document_id,
        )
    )
    session.flush()

    get_event_bus().publish(
        DomainEvent(
            event_type="contract_award",
            payload={
                "contract_id": contract.contract_id,
                "title": contract.title,
                "agency": agency.canonical_name,
                "vendor": vendor.canonical_name,
                "value": contract.value,
            },
        )
    )
    return contract


def ingest_register_document(session: Session, document: RegisterDocument) -> SourceDocument:
    source = SourceDocument(
        url=document.html_url,
        source_name="federalregister.gov",
        document_type=document.document_type,
        publication_date=_as_datetime(document.publication_date),
        ingestion_timestamp=datetime.now(timezone.utc),
    )
    session.add(source)
    session.flush()

    get_event_bus().publish(
        DomainEvent(
            event_type="regulatory_action",
            payload={
                "document_number": document.document_number,
                "title": document.title,
                "agencies": document.agencies,
                "source_url": document.html_url,
            },
        )
    )
    return source
