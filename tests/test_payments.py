from decimal import Decimal

import pytest

from app.services.accounts import parse_payment_amount


@pytest.mark.parametrize(
    ("value", "expected"),
    [("100", Decimal("100.00")), ("100.5", Decimal("100.50")), ("100,50", Decimal("100.50"))],
)
def test_parse_payment_amount(value: str, expected: Decimal) -> None:
    assert parse_payment_amount(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "zero", "0", "-1", "1.234", "1e2", "10000000000", "9999999999.999", "9" * 100],
)
def test_parse_payment_amount_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError, match="invalid_amount"):
        parse_payment_amount(value)
