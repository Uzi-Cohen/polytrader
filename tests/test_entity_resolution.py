import json

from polytrader.contracts.entity_resolution import (
    normalize_agency_name,
    normalize_organization_name,
    resolve_agency,
    resolve_organization,
)


def test_normalize_agency_name_maps_known_aliases():
    assert normalize_agency_name("DoD") == "Department of Defense"
    assert normalize_agency_name("Dept. of Defense") == "Department of Defense"
    assert normalize_agency_name("Department of Defense") == "Department of Defense"
    assert normalize_agency_name("DHS") == "Department of Homeland Security"


def test_normalize_agency_name_passes_through_unknown_names():
    assert normalize_agency_name("Small Regional Water Authority") == "Small Regional Water Authority"


def test_normalize_organization_name_strips_corporate_suffixes():
    assert normalize_organization_name("Example Corp.") == "EXAMPLE"
    assert normalize_organization_name("Example Inc") == "EXAMPLE"
    assert normalize_organization_name("Example LLC") == "EXAMPLE"


def test_resolve_agency_creates_one_canonical_row_for_variants(db_session):
    a1 = resolve_agency(db_session, "DoD")
    a2 = resolve_agency(db_session, "Dept of Defense")
    a3 = resolve_agency(db_session, "Department of Defense")

    assert a1.agency_id == a2.agency_id == a3.agency_id
    assert a1.canonical_name == "Department of Defense"


def test_resolve_agency_records_aliases_seen(db_session):
    resolve_agency(db_session, "DoD")
    agency = resolve_agency(db_session, "US Department of Defense")
    aliases = json.loads(agency.aliases or "[]")
    assert "US Department of Defense" in aliases


def test_resolve_organization_creates_one_canonical_row_for_suffix_variants(db_session):
    o1 = resolve_organization(db_session, "Example Corp")
    o2 = resolve_organization(db_session, "Example Corp.")
    o3 = resolve_organization(db_session, "Example LLC")

    assert o1.org_id == o2.org_id == o3.org_id


def test_resolve_organization_distinguishes_different_companies(db_session):
    o1 = resolve_organization(db_session, "Example Corp")
    o2 = resolve_organization(db_session, "Other Company Inc")
    assert o1.org_id != o2.org_id
