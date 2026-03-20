from __future__ import annotations

ALLOWED_QUERY_PARAMS: dict[str, set[str]] = {
    "/customer": {"name", "organizationNumber", "count", "from", "sorting", "fields"},
    "/employee": {"email", "firstName", "lastName", "count", "from", "sorting", "fields"},
    "/project": {"name", "customerId", "count", "from", "sorting", "fields"},
    "/product": {"name", "number", "count", "from", "sorting", "fields"},
    "/invoice": {
        "customerId",
        "invoiceDateFrom",
        "invoiceDateTo",
        "count",
        "from",
        "sorting",
        "fields",
    },
    "/order": {"customerId", "orderDateFrom", "orderDateTo", "count", "from", "sorting", "fields"},
    "/invoice/paymentType": {"count", "fields"},
    "/salary/type": {"name", "count", "fields"},
    "/travelExpense": {"employeeId", "count", "fields", "sorting"},
}

ALLOWED_FIELDS: dict[str, set[str]] = {
    "/customer": {"id", "name", "email", "organizationNumber", "isCustomer"},
    "/employee": {"id", "firstName", "lastName", "email", "displayName"},
    "/project": {"id", "name", "customer", "projectManager", "isFixedPrice", "fixedPrice"},
    "/product": {"id", "name", "number", "priceExcludingVatCurrency", "vatType"},
    "/invoice": {
        "id",
        "invoiceDate",
        "invoiceNumber",
        "customer",
        "amountExcludingVat",
        "amountExcludingVatCurrency",
        "amountIncludingVat",
        "paidAmount",
        "amountOutstanding",
        "invoiceStatus",
        "comment",
        "reference",
    },
    "/order": {"id", "customer", "orderDate", "deliveryDate", "orderLines"},
    "/invoice/paymentType": {"id", "description", "name"},
    "/salary/type": {"id", "name", "number"},
    "/travelExpense": {"id", "title", "date", "employee"},
}

