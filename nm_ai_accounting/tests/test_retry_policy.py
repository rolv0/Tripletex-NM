from execution.retry_policy import RetryPolicy


def test_retry_policy_does_not_retry_illegal_fields_filter_error():
    policy = RetryPolicy(max_retries=1)
    error_text = "Tripletex GET /invoice failed: 400 body={\"message\":\"Illegal field in fields filter: invoiceStatus.\"}"
    assert policy.should_retry(0, error_text) is False


def test_retry_policy_retries_missing_required_field_error():
    policy = RetryPolicy(max_retries=1)
    error_text = "Tripletex POST /salary/transaction failed: 422 body={\"message\":\"Validation failed.\",\"validationMessages\":[{\"message\":\"Kan ikke være null.\"}]}"
    assert policy.should_retry(0, error_text) is True


def test_retry_policy_does_not_retry_salary_spec_schema_errors():
    policy = RetryPolicy(max_retries=1)
    error_text = (
        "Tripletex POST /salary/transaction failed: 422 body={"
        "\"validationMessages\":[{\"field\":\"payslips.specifications.count\",\"message\":\"Kan ikke være null.\"}]}"
    )
    assert policy.should_retry(0, error_text) is False


def test_retry_policy_does_not_retry_missing_employment_error():
    policy = RetryPolicy(max_retries=1)
    error_text = (
        "Tripletex POST /salary/transaction failed: 422 body={"
        "\"validationMessages\":[{\"field\":\"employee\",\"message\":\"Ansatt nr. er ikke registrert med et arbeidsforhold i perioden.\"}]}"
    )
    assert policy.should_retry(0, error_text) is False
