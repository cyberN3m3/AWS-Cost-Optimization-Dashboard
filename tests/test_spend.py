from datetime import date

import spend


class FakeCostExplorer:
    def get_cost_and_usage(self, TimePeriod, Granularity, Metrics, GroupBy=None):
        if GroupBy is None:   # month-to-date total for the forecast
            return {'ResultsByTime': [{'Total': {'UnblendedCost': {'Amount': '40.00'}}}]}

        def group(svc, amount):
            return {'Keys': [svc], 'Metrics': {'UnblendedCost': {'Amount': str(amount)}}}
        return {'ResultsByTime': [
            {'TimePeriod': {'Start': '2026-08-01'}, 'Groups': [
                group('Amazon EC2', 50), group('Amazon RDS', 30), group('Amazon S3', 5)]},
            {'TimePeriod': {'Start': '2026-09-01'}, 'Groups': [
                group('Amazon EC2', 90), group('Amazon RDS', 30), group('Amazon S3', 4),
                group('AWS WAF', 6)]},
        ]}

    def get_cost_forecast(self, **kwargs):
        return {'Total': {'Amount': '95.50'}}

    def get_savings_plans_purchase_recommendation(self, **kwargs):
        return {'SavingsPlansPurchaseRecommendation': {'SavingsPlansPurchaseRecommendationSummary': {
            'HourlyCommitmentToPurchase': '0.05', 'EstimatedMonthlySavingsAmount': '12.40',
            'EstimatedSavingsPercentage': '18.5'}}}


def test_spend_summary_month_on_month():
    result = spend.spend_summary(FakeCostExplorer(), today=date(2026, 10, 27))
    assert result['last_month'] == 'September 2026'
    assert result['total_last_month'] == 130.0
    assert result['total_month_before'] == 85.0
    assert result['change'] == 45.0
    assert result['change_pct'] == 52.9
    assert result['top_services'][0]['service'] == 'Amazon EC2'
    assert [r['service'] for r in result['biggest_risers']] == ['Amazon EC2', 'AWS WAF']
    assert result['forecast_this_month'] == 135.5


def test_savings_plan_recommendation():
    plan = spend.savings_plan_recommendation(FakeCostExplorer())
    assert plan == {'hourly_commitment': 0.05, 'monthly_saving': 12.4, 'saving_pct': 18.5}


def test_month_helpers():
    assert spend.previous_month_start(date(2026, 1, 15)) == date(2025, 12, 1)
