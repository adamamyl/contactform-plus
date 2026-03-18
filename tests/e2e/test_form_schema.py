from __future__ import annotations

import os

import pytest

_FORM_URL = os.environ.get("FORM_BASE_URL", "")
if not _FORM_URL:
    pytest.skip("FORM_BASE_URL not set", allow_module_level=True)

import schemathesis  # noqa: E402
import schemathesis.openapi  # noqa: E402
from hypothesis import HealthCheck  # noqa: E402
from hypothesis import settings as h_settings  # noqa: E402
from schemathesis.specs.openapi.checks import (  # noqa: E402
    negative_data_rejection,
    positive_data_acceptance,
)

schema = schemathesis.openapi.from_url(f"{_FORM_URL}/openapi.json")

# /metrics returns text/plain (Prometheus format) — not JSON as FastAPI auto-documents.
# /success is an HTML page that intentionally ignores unknown query params.
# /attachments: schemathesis sends empty multipart bodies that FastAPI sees as missing fields.
_SKIP_PATHS = {"/metrics", "/success", "/attachments"}

# /api/submit: event_name is a free string in the schema but must be one of the
# runtime-configured event names. Schemathesis generates arbitrary valid strings
# that the route rejects with 422 — suppress the positive-data-acceptance check.
_EXCLUDE_POSITIVE_ACCEPTANCE = {"/api/submit"}


@schema.parametrize()
@h_settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
def test_form_api_conformance(case: schemathesis.Case) -> None:
    if case.path in _SKIP_PATHS:
        pytest.skip(f"Excluded from schema conformance: {case.path}")
    # Exclude both positive-data-acceptance and negative-data-rejection for /api/submit:
    # event_name accepts only runtime-configured values (not any string), and the rate limit
    # may fire before Pydantic validation, causing 429 on invalid payloads.
    excluded = (
        [positive_data_acceptance, negative_data_rejection]
        if case.path in _EXCLUDE_POSITIVE_ACCEPTANCE
        else []
    )
    case.call_and_validate(excluded_checks=excluded)
