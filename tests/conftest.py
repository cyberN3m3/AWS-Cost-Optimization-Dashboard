import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lambda'))

os.environ.setdefault('AWS_ACCESS_KEY_ID', 'testing')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'testing')
os.environ.setdefault('AWS_SESSION_TOKEN', 'testing')
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')

REGION = 'us-east-1'


class FakePriceBook:
    """Fixed prices so tests do not depend on the Price List API."""
    PRICES = {'t3.large': 60.74, 't3.medium': 30.37, 't3.micro': 7.59}

    def monthly(self, instance_type, region):
        return self.PRICES.get(instance_type), 'price-list'


@pytest.fixture
def price_book():
    return FakePriceBook()


@pytest.fixture
def later(monkeypatch):
    """Move the scanner's clock forward so new moto resources look old."""
    import checks

    def _shift(days):
        future = datetime.now(timezone.utc) + timedelta(days=days)
        monkeypatch.setattr(checks, 'utcnow', lambda: future)
        return future
    return _shift
