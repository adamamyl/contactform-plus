from __future__ import annotations

import os

import pytest

_FORM_URL = os.environ.get("FORM_BASE_URL", "")
if not _FORM_URL:
    pytest.skip("FORM_BASE_URL not set", allow_module_level=True)

import schemathesis  # noqa: E402
import schemathesis.openapi  # noqa: E402
from hypothesis import HealthCheck, settings as h_settings  # noqa: E402

schema = schemathesis.openapi.from_url(f"{_FORM_URL}/openapi.json")


@schema.parametrize()
@h_settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
def test_form_api_conformance(case: schemathesis.Case) -> None:
    case.call_and_validate()
