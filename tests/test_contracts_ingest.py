from datetime import date

from polytrader.contracts.federal_register_client import RegisterDocument
from polytrader.contracts.ingest import ingest_contract_award, ingest_register_document
from polytrader.contracts.usaspending_client import ContractAward
from polytrader.core.events import get_event_bus
from polytrader.core.models import Contract, GovernmentAgency, Organization, SourceDocument


def make_award(**overrides) -> ContractAward:
    defaults = dict(
        award_id="W91CRB-26-C-0001",
        generated_internal_id="CONT_AWD_1",
        title="AI-enabled autonomous systems",
        recipient_name="Example Corp",
        awarding_agency="DoD",
        awarding_sub_agency="Department of the Army",
        amount=180_000_000.0,
        start_date=date(2026, 3, 1),
        end_date=date(2027, 3, 1),
        permalink="https://www.usaspending.gov/award/CONT_AWD_1",
        raw={},
    )
    defaults.update(overrides)
    return ContractAward(**defaults)


def test_ingest_contract_award_creates_linked_entities(db_session):
    award = make_award()
    contract = ingest_contract_award(db_session, award)

    assert db_session.get(Contract, contract.contract_id) is not None
    agency = db_session.get(GovernmentAgency, contract.awarding_agency_id)
    assert agency.canonical_name == "Department of Defense"
    vendor = db_session.get(Organization, contract.vendor_org_id)
    assert vendor.canonical_name == "EXAMPLE"
    source = db_session.get(SourceDocument, contract.source_document_id)
    assert source.url == award.permalink
    assert source.source_name == "usaspending.gov"


def test_ingest_contract_award_publishes_domain_event(db_session):
    received = []
    get_event_bus().subscribe("contract_award", received.append)

    award = make_award()
    ingest_contract_award(db_session, award)

    assert len(received) == 1
    assert received[0].payload["vendor"] == "EXAMPLE"
    assert received[0].payload["agency"] == "Department of Defense"


def test_ingest_contract_award_updates_existing_contract_idempotently(db_session):
    award = make_award()
    ingest_contract_award(db_session, award)
    updated_award = make_award(amount=200_000_000.0)
    ingest_contract_award(db_session, updated_award)

    contract = db_session.get(Contract, award.generated_internal_id)
    assert contract.value == 200_000_000.0
    # still exactly one contract row, not a duplicate
    assert db_session.query(Contract).count() == 1


def test_ingest_register_document_creates_source(db_session):
    doc = RegisterDocument(
        document_number="2026-1",
        title="Executive Order on AI",
        document_type="Presidential Document",
        publication_date=date(2026, 8, 1),
        agencies=["Executive Office of the President"],
        abstract="...",
        html_url="https://www.federalregister.gov/documents/2026/08/01/2026-1/example",
        raw={},
    )
    source = ingest_register_document(db_session, doc)
    assert source.source_name == "federalregister.gov"
    assert source.url == doc.html_url
