from services.billing.pricing import Pricer


def test_price():
    assert Pricer().price(2) == 4
