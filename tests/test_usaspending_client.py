from polytrader.contracts.usaspending_client import _parse_award

RAW_AWARD = {
    "Award ID": "W91CRB-26-C-0001",
    "Recipient Name": "EXAMPLE CORP",
    "Awarding Agency": "Department of Defense",
    "Awarding Sub Agency": "Department of the Army",
    "Award Amount": 180_000_000.0,
    "Start Date": "2026-03-01",
    "End Date": "2027-03-01",
    "Description": "AI-ENABLED AUTONOMOUS SYSTEMS",
    "generated_internal_id": "CONT_AWD_W91CRB26C0001",
}


def test_parses_award_fields():
    award = _parse_award(RAW_AWARD)
    assert award.award_id == "W91CRB-26-C-0001"
    assert award.recipient_name == "EXAMPLE CORP"
    assert award.awarding_agency == "Department of Defense"
    assert award.amount == 180_000_000.0
    assert award.start_date.isoformat() == "2026-03-01"
    assert award.end_date.isoformat() == "2027-03-01"
    assert award.permalink == "https://www.usaspending.gov/award/CONT_AWD_W91CRB26C0001"


def test_handles_missing_optional_fields():
    minimal = {"generated_internal_id": "CONT_AWD_X"}
    award = _parse_award(minimal)
    assert award.recipient_name == "Unknown recipient"
    assert award.awarding_agency == "Unknown agency"
    assert award.amount is None
    assert award.start_date is None
    assert award.title == "(untitled award)"


def test_handles_malformed_dates_gracefully():
    raw = dict(RAW_AWARD, **{"Start Date": "not-a-date"})
    award = _parse_award(raw)
    assert award.start_date is None
