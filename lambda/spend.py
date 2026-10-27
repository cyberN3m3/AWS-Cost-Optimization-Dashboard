"""What the account actually spends, from the Cost Explorer API.

Answers the question clients ask first: "why is our bill going up?"
Each Cost Explorer request costs $0.01, and a scan makes three or four.
"""

from datetime import date, timedelta


def month_start(day):
    return day.replace(day=1)


def previous_month_start(day):
    return month_start(month_start(day) - timedelta(days=1))


def spend_summary(ce, today=None, top_n=5):
    today = today or date.today()
    this_month = month_start(today)
    last_month = previous_month_start(today)
    month_before = previous_month_start(last_month)

    resp = ce.get_cost_and_usage(
        TimePeriod={'Start': month_before.isoformat(), 'End': this_month.isoformat()},
        Granularity='MONTHLY',
        Metrics=['UnblendedCost'],
        GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}],
    )
    months = {}
    for period in resp.get('ResultsByTime', []):
        start = period['TimePeriod']['Start']
        months[start] = {
            g['Keys'][0]: float(g['Metrics']['UnblendedCost']['Amount'])
            for g in period.get('Groups', [])
        }
    before = months.get(month_before.isoformat(), {})
    last = months.get(last_month.isoformat(), {})

    total_before = round(sum(before.values()), 2)
    total_last = round(sum(last.values()), 2)
    change = round(total_last - total_before, 2)
    change_pct = round(change / total_before * 100, 1) if total_before else None

    top_services = sorted(last.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    deltas = {svc: last.get(svc, 0) - before.get(svc, 0) for svc in set(last) | set(before)}
    risers = sorted(((s, d) for s, d in deltas.items() if d > 0.01),
                    key=lambda kv: kv[1], reverse=True)[:top_n]

    return {
        'last_month': last_month.strftime('%B %Y'),
        'month_before': month_before.strftime('%B %Y'),
        'total_last_month': total_last,
        'total_month_before': total_before,
        'change': change,
        'change_pct': change_pct,
        'top_services': [
            {'service': s, 'cost': round(c, 2),
             'share_pct': round(c / total_last * 100, 1) if total_last else 0}
            for s, c in top_services
        ],
        'biggest_risers': [
            {'service': s, 'increase': round(d, 2),
             'from': round(before.get(s, 0), 2), 'to': round(last.get(s, 0), 2)}
            for s, d in risers
        ],
        'forecast_this_month': forecast_this_month(ce, today),
    }


def forecast_this_month(ce, today):
    """AWS's own forecast for the current month, or None early in the month."""
    next_month = month_start(month_start(today) + timedelta(days=32))
    start = today + timedelta(days=1)
    if start >= next_month:
        return None
    try:
        resp = ce.get_cost_forecast(
            TimePeriod={'Start': start.isoformat(), 'End': next_month.isoformat()},
            Metric='UNBLENDED_COST', Granularity='MONTHLY')
        rest_of_month = float(resp['Total']['Amount'])
        so_far = ce.get_cost_and_usage(
            TimePeriod={'Start': month_start(today).isoformat(), 'End': start.isoformat()},
            Granularity='MONTHLY', Metrics=['UnblendedCost'])
        spent = sum(float(p['Total']['UnblendedCost']['Amount'])
                    for p in so_far.get('ResultsByTime', []))
        return round(spent + rest_of_month, 2)
    except Exception as exc:  # not enough history, or Cost Explorer just enabled
        print(f"Forecast unavailable: {exc}")
        return None


def savings_plan_recommendation(ce):
    """AWS's own Compute Savings Plan recommendation (1 year, no upfront)."""
    try:
        resp = ce.get_savings_plans_purchase_recommendation(
            SavingsPlansType='COMPUTE_SP', TermInYears='ONE_YEAR',
            PaymentOption='NO_UPFRONT', LookbackPeriodInDays='THIRTY_DAYS')
    except Exception as exc:
        print(f"Savings Plans recommendation unavailable: {exc}")
        return None
    summary = resp.get('SavingsPlansPurchaseRecommendation', {}) \
                  .get('SavingsPlansPurchaseRecommendationSummary')
    if not summary:
        return None
    monthly = float(summary.get('EstimatedMonthlySavingsAmount', 0) or 0)
    if monthly <= 0:
        return None
    return {
        'hourly_commitment': float(summary.get('HourlyCommitmentToPurchase', 0) or 0),
        'monthly_saving': round(monthly, 2),
        'saving_pct': float(summary.get('EstimatedSavingsPercentage', 0) or 0),
    }
