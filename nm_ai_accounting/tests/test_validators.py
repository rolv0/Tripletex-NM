from tripletex.validators import canonical_endpoint, validate_request


def test_canonical_endpoint_action_path_maps_to_order():
    assert canonical_endpoint("/order/401960073/:invoice") == "/order"


def test_canonical_endpoint_maps_employee_employment_details():
    assert canonical_endpoint("/employee/employment/details") == "/employee/employment/details"


def test_validate_request_removes_illegal_invoice_fields():
    result = validate_request(
        method="GET",
        path="/invoice",
        params={"fields": "id,customerId,amountIncludingVat", "count": 20},
        payload=None,
        allowed_endpoints={"/invoice"},
    )
    assert result.params["fields"] == "id,amountIncludingVat"


def test_validate_request_removes_invoice_status_field():
    result = validate_request(
        method="GET",
        path="/invoice",
        params={"fields": "id,invoiceStatus,amountOutstanding", "count": 20},
        payload=None,
        allowed_endpoints={"/invoice"},
    )
    assert result.params["fields"] == "id,amountOutstanding"


def test_validate_request_keeps_put_action_params():
    result = validate_request(
        method="PUT",
        path="/order/123/:invoice",
        params={"invoiceDate": "2026-03-20", "sendToCustomer": False, "sendType": "MANUAL"},
        payload=None,
        allowed_endpoints={"/order"},
    )
    assert result.params["sendType"] == "MANUAL"


def test_validate_request_keeps_employee_employment_query_params():
    result = validate_request(
        method="GET",
        path="/employee/employment",
        params={"employeeId": 123, "count": 10, "fields": "id,startDate,endDate,unknownField"},
        payload=None,
        allowed_endpoints={"/employee/employment"},
    )
    assert result.params["employeeId"] == 123
    assert result.params["fields"] == "id,startDate,endDate"
