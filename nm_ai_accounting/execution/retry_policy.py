from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 1

    def should_retry(self, attempt: int, error_text: str) -> bool:
        if attempt >= self.max_retries:
            return False

        normalized = error_text.lower()

        # Do not retry deterministic contract/schema failures.
        if any(
            token in normalized
            for token in (
                "illegal field in fields filter",
                "does not match a field in the model",
                "is not allowed for selected workflow",
                "endpoint ",
                "payslips.specifications.count",
                "payslips.specifications.rate",
                "registrert med et arbeidsforhold",
                "postings.row",
                "uten posteringer",
                "systemgenererte",
                "prosjektleder",
                "project manager",
                "projektleiter",
            )
        ):
            return False

        if any(
            token in normalized
            for token in (
                "returned non-json response",
                "<!doctype html",
                "<html",
                "csrftoken",
                "uforutsett feil har oppstatt",
                "uforutsett feil har oppstått",
                "driftstatusside",
            )
        ):
            return True

        return any(
            token in normalized
            for token in (
                "validation",
                "required",
                "kan ikke være null",
                "kan ikke vaere null",
                "missing",
            )
        )
