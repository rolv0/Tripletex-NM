from workflows.ledger_correction import _parse_asset_specs, _parse_voucher_date


def test_parse_asset_specs_from_year_end_prompt():
    prompt = (
        "Effectuez la cloture annuelle simplifiee pour 2025: "
        "Programvare (496650 NOK, 4 ans lineaire, compte 1250), "
        "Maskiner (240000 NOK, 5 ans lineaire, compte 1280)."
    )
    specs = _parse_asset_specs(prompt)
    assert len(specs) == 2
    assert specs[0]["name"] == "Programvare"
    assert specs[0]["assetAccountNumber"] == "1250"
    assert specs[0]["lifetimeYears"] == 4


def test_parse_voucher_date_uses_year_end():
    prompt = "Effectuez la cloture annuelle simplifiee pour 2025."
    assert _parse_voucher_date(prompt) == "2025-12-31"
