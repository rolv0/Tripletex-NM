from tripletex.validators import canonical_endpoint, validate_request


def test_canonical_endpoint_action_path_maps_to_order():
    assert canonical_endpoint("/order/401960073/:invoice") == "/order"


def test_validate_request_removes_illegal_invoice_fields():
    result = validate_request(
        method="GET",
        path="/invoice",
        params={"fields": "id,customerId,amountIncludingVat", "count": 20},
        payload=None,
        allowed_endpoints={"/invoice"},
    )
    assert result.params["fields"] == "id,amountIncludingVat"


def test_validate_request_keeps_put_action_params():
    result = validate_request(
        method="PUT",
        path="/order/123/:invoice",
        params={"invoiceDate": "2026-03-20", "sendToCustomer": False, "sendType": "MANUAL"},
        payload=None,
        allowed_endpoints={"/order"},
    )
    assert result.params["sendType"] == "MANUAL"
