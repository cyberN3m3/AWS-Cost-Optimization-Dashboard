import json

import pricing


def product(usd):
    return json.dumps({'terms': {'OnDemand': {'X': {'priceDimensions': {
        'Y': {'unit': 'Hrs', 'pricePerUnit': {'USD': usd}}}}}}})


class FakePricingClient:
    def __init__(self, usd=None, fail=False):
        self.usd, self.fail, self.calls = usd, fail, 0

    def get_products(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError('no network')
        return {'PriceList': [product(self.usd)] if self.usd else []}


def test_parse_on_demand_hourly():
    assert pricing.parse_on_demand_hourly([product('0.0832000000')]) == 0.0832
    assert pricing.parse_on_demand_hourly([product('0.0000000000')]) is None


def test_price_book_uses_price_list_and_caches():
    client = FakePricingClient(usd='0.1000000000')
    book = pricing.Ec2PriceBook(client)
    assert book.monthly('m5.large', 'eu-west-2') == (73.0, 'price-list')
    book.monthly('m5.large', 'eu-west-2')
    assert client.calls == 1


def test_price_book_falls_back_to_estimate():
    book = pricing.Ec2PriceBook(FakePricingClient(fail=True))
    assert book.monthly('t3.large', 'us-east-1') == (60.74, 'estimate')
    assert book.monthly('x9.mega', 'us-east-1') == (None, 'estimate')
