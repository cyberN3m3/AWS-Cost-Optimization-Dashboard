"""Prices used to estimate monthly cost and savings.

EC2 on-demand prices come from the AWS Price List API, so they match the
instance type and region. Everything else uses published us-east-1 list
prices, which are close enough to rank savings but are marked as estimates
in the report.
"""

import json

HOURS_PER_MONTH = 730

# Published us-east-1 list prices (USD). Used as estimates.
EBS_GB_MONTH = {
    'gp2': 0.10, 'gp3': 0.08, 'io1': 0.125, 'io2': 0.125,
    'st1': 0.045, 'sc1': 0.015, 'standard': 0.05,
}
SNAPSHOT_GB_MONTH = 0.05
PUBLIC_IPV4_HOUR = 0.005          # every public IPv4 address, attached or not
LOAD_BALANCER_HOUR = 0.0225       # ALB or NLB, before capacity units
NAT_GATEWAY_HOUR = 0.045          # before data processing charges
LOG_STORAGE_GB_MONTH = 0.03

# Used only when the Price List API cannot be reached or has no match.
EC2_FALLBACK_HOURLY = {
    't2.micro': 0.0116, 't2.small': 0.023, 't2.medium': 0.0464, 't2.large': 0.0928,
    't3.nano': 0.0052, 't3.micro': 0.0104, 't3.small': 0.0208, 't3.medium': 0.0416,
    't3.large': 0.0832, 't3.xlarge': 0.1664, 't3.2xlarge': 0.3328,
    'm5.large': 0.096, 'm5.xlarge': 0.192, 'm6i.large': 0.096, 'm6i.xlarge': 0.192,
    'c5.large': 0.085, 'c5.xlarge': 0.17, 'r5.large': 0.126, 'r5.xlarge': 0.252,
}


class Ec2PriceBook:
    """Looks up Linux on-demand prices once per instance type and region."""

    def __init__(self, pricing_client=None):
        self._client = pricing_client
        self._cache = {}

    def hourly(self, instance_type, region):
        """Return (hourly_price_usd, source). source is 'price-list' or 'estimate'."""
        key = (instance_type, region)
        if key not in self._cache:
            self._cache[key] = self._lookup(instance_type, region)
        return self._cache[key]

    def monthly(self, instance_type, region):
        price, source = self.hourly(instance_type, region)
        if price is None:
            return None, source
        return round(price * HOURS_PER_MONTH, 2), source

    def _lookup(self, instance_type, region):
        if self._client is not None:
            try:
                resp = self._client.get_products(
                    ServiceCode='AmazonEC2',
                    Filters=[
                        {'Type': 'TERM_MATCH', 'Field': 'instanceType', 'Value': instance_type},
                        {'Type': 'TERM_MATCH', 'Field': 'regionCode', 'Value': region},
                        {'Type': 'TERM_MATCH', 'Field': 'operatingSystem', 'Value': 'Linux'},
                        {'Type': 'TERM_MATCH', 'Field': 'tenancy', 'Value': 'Shared'},
                        {'Type': 'TERM_MATCH', 'Field': 'preInstalledSw', 'Value': 'NA'},
                        {'Type': 'TERM_MATCH', 'Field': 'capacitystatus', 'Value': 'Used'},
                    ],
                    MaxResults=1,
                )
                price = parse_on_demand_hourly(resp.get('PriceList', []))
                if price is not None:
                    return price, 'price-list'
            except Exception as exc:  # network, permissions or throttling
                print(f"Price List lookup failed for {instance_type} in {region}: {exc}")
        fallback = EC2_FALLBACK_HOURLY.get(instance_type)
        return fallback, 'estimate'


def parse_on_demand_hourly(price_list):
    """Pull the hourly USD price out of a Price List API product document."""
    for raw in price_list:
        product = json.loads(raw) if isinstance(raw, str) else raw
        for term in product.get('terms', {}).get('OnDemand', {}).values():
            for dimension in term.get('priceDimensions', {}).values():
                usd = dimension.get('pricePerUnit', {}).get('USD')
                if usd is not None and float(usd) > 0:
                    return float(usd)
    return None


def ebs_monthly(size_gb, volume_type):
    return round(size_gb * EBS_GB_MONTH.get(volume_type, 0.10), 2)
