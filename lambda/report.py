"""Turns findings into the ranked savings report: JSON, HTML and an email summary."""

import html
from collections import defaultdict

REPORT_VERSION = '3.0'

CHECK_LABELS = {
    'idle_ec2': 'Idle EC2 instances',
    'unattached_ebs': 'Unattached EBS volumes',
    'gp2_to_gp3': 'gp2 volumes to move to gp3',
    'old_snapshot': 'Old EBS snapshots',
    'unattached_eip': 'Unattached Elastic IPs',
    'idle_nat': 'Idle NAT gateways',
    'idle_load_balancer': 'Idle load balancers',
    'log_retention': 'Log groups with no retention',
    's3_no_lifecycle': 'S3 buckets with no lifecycle rules',
    'savings_plan': 'Savings Plan',
}
RISK_ORDER = {'Low': 0, 'Medium': 1, 'High': 2}


def build_report(findings, spend, savings_plan, regions, scanned_at, account_id=''):
    ranked = sorted(findings, key=lambda f: (-f['monthly_saving'], RISK_ORDER.get(f['risk'], 3)))
    by_check = defaultdict(lambda: {'count': 0, 'monthly_saving': 0.0})
    for f in ranked:
        by_check[f['check']]['count'] += 1
        by_check[f['check']]['monthly_saving'] += f['monthly_saving']
    waste = round(sum(f['monthly_saving'] for f in ranked), 2)
    plan_saving = savings_plan['monthly_saving'] if savings_plan else 0
    return {
        'version': REPORT_VERSION,
        'sample': False,
        'scanned_at': scanned_at,
        'account_id': account_id,
        'regions_scanned': regions,
        'totals': {
            'findings': len(ranked),
            'monthly_saving': waste,
            'yearly_saving': round(waste * 12, 2),
            'savings_plan_monthly': plan_saving,
        },
        'spend': spend,
        'savings_plan': savings_plan,
        'by_check': {k: {'label': CHECK_LABELS.get(k, k), 'count': v['count'],
                         'monthly_saving': round(v['monthly_saving'], 2)}
                     for k, v in by_check.items()},
        'findings': ranked,
    }


def money(value):
    return f"${value:,.2f}"


def email_summary(report, report_location=''):
    t = report['totals']
    lines = [
        f"AWS cost report, {report['scanned_at']}",
        f"Regions scanned: {len(report['regions_scanned'])}",
        '',
    ]
    spend = report.get('spend')
    if spend:
        change = spend['change_pct']
        trend = f"{'up' if spend['change'] > 0 else 'down'} {abs(change)}%" if change is not None else 'no earlier month to compare'
        lines += [
            f"Spend in {spend['last_month']}: {money(spend['total_last_month'])} "
            f"({trend} on {spend['month_before']})",
        ]
        if spend.get('forecast_this_month') is not None:
            lines.append(f"Forecast for this month: {money(spend['forecast_this_month'])}")
        if spend['biggest_risers']:
            lines.append('Biggest increases:')
            lines += [f"  - {r['service']}: +{money(r['increase'])}" for r in spend['biggest_risers'][:3]]
        lines.append('')
    lines += [
        f"Waste found: {t['findings']} items, about {money(t['monthly_saving'])} a month "
        f"({money(t['yearly_saving'])} a year)",
    ]
    if report.get('savings_plan'):
        sp = report['savings_plan']
        lines.append(f"Savings Plan: AWS estimates {money(sp['monthly_saving'])} a month "
                     f"with a {money(sp['hourly_commitment'])}/hour Compute Savings Plan")
    if report['findings']:
        lines += ['', 'Top savings:']
        for i, f in enumerate(report['findings'][:5], 1):
            lines.append(f"  {i}. {money(f['monthly_saving'])}/month  {f['title']}  "
                         f"{f['resource_id']} ({f['region']})  risk {f['risk']}")
            lines.append(f"     {f['action']}")
    else:
        lines += ['', 'No waste found this week.']
    if report_location:
        lines += ['', f"Full report: {report_location}"]
    lines += ['', 'Nothing was changed in your account. This scanner is read-only.']
    return '\n'.join(lines)


def render_html(report):
    e = html.escape
    t = report['totals']
    spend = report.get('spend')
    rows = []
    for i, f in enumerate(report['findings'], 1):
        est = ' <span class="est">est.</span>' if f['saving_is_estimate'] else ''
        rows.append(
            f"<tr><td>{i}</td><td><strong>{e(f['title'])}</strong><br>"
            f"<code>{e(str(f['resource_id']))}</code>"
            f"{(' &middot; ' + e(f['resource_name'])) if f['resource_name'] else ''}"
            f"<div class='muted'>{e(f['detail'])}</div></td>"
            f"<td class='nowrap'>{e(f['region'])}</td>"
            f"<td class='num'>{money(f['monthly_saving'])}{est}</td>"
            f"<td><span class='pill {f['effort'].lower()}'>{e(f['effort'])}</span></td>"
            f"<td><span class='pill {f['risk'].lower()}'>{e(f['risk'])}</span></td>"
            f"<td>{e(f['action'])}</td></tr>")
    spend_html = ''
    if spend:
        change = spend['change_pct']
        trend = (f"{'up' if spend['change'] > 0 else 'down'} {abs(change)}% on {e(spend['month_before'])}"
                 if change is not None else 'no earlier month to compare')
        services = ''.join(
            f"<tr><td>{e(s['service'])}</td><td class='num'>{money(s['cost'])}</td>"
            f"<td class='num'>{s['share_pct']}%</td></tr>" for s in spend['top_services'])
        risers = ''.join(
            f"<tr><td>{e(r['service'])}</td><td class='num'>{money(r['from'])}</td>"
            f"<td class='num'>{money(r['to'])}</td><td class='num up'>+{money(r['increase'])}</td></tr>"
            for r in spend['biggest_risers']) or "<tr><td colspan='4'>No service went up.</td></tr>"
        forecast = (f"<p>Forecast for this month: <strong>{money(spend['forecast_this_month'])}</strong></p>"
                    if spend.get('forecast_this_month') is not None else '')
        spend_html = f"""
<h2>Where the money goes</h2>
<p>Spend in {e(spend['last_month'])}: <strong>{money(spend['total_last_month'])}</strong>, {trend}.</p>
{forecast}
<div class="two">
<table><caption>Top services, {e(spend['last_month'])}</caption>
<tr><th>Service</th><th class='num'>Cost</th><th class='num'>Share</th></tr>{services}</table>
<table><caption>Biggest increases</caption>
<tr><th>Service</th><th class='num'>Before</th><th class='num'>After</th><th class='num'>Change</th></tr>{risers}</table>
</div>"""
    plan = report.get('savings_plan')
    plan_html = (f"<p class='callout'>AWS estimates a 1-year Compute Savings Plan of "
                 f"{money(plan['hourly_commitment'])}/hour would save about "
                 f"<strong>{money(plan['monthly_saving'])} a month</strong> ({plan['saving_pct']}%). "
                 f"Only commit if this usage will continue for a year.</p>") if plan else ''
    sample_note = ("<p class='callout'>Sample data: this report shows made-up findings to "
                   "demonstrate the format.</p>") if report.get('sample') else ''
    table = ("<table class='findings'><tr><th>#</th><th>Finding</th><th>Region</th>"
             "<th class='num'>Saving / month</th><th>Effort</th><th>Risk</th><th>Recommended action</th></tr>"
             + ''.join(rows) + "</table>") if rows else "<p>No waste found.</p>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AWS cost report {e(report['scanned_at'])}</title>
<style>
body{{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1d2433;max-width:1080px;margin:32px auto;padding:0 20px;line-height:1.45}}
h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:19px;margin:32px 0 8px}}
.muted{{color:#5b6475;font-size:13px}} code{{font-size:12px;background:#f1f3f7;padding:1px 4px;border-radius:3px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:20px 0}}
.card{{border:1px solid #dde1e8;border-radius:8px;padding:14px}} .card b{{display:block;font-size:24px}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{text-align:left;padding:8px;border-bottom:1px solid #e6e9ef;vertical-align:top}}
th{{background:#f6f7f9;font-weight:600}} caption{{text-align:left;font-weight:600;padding:6px 0}}
.num,.nowrap{{white-space:nowrap}} .num{{text-align:right}} .up{{color:#b42318}} .est{{color:#8a6d00;font-size:11px}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:20px}} @media(max-width:760px){{.two{{grid-template-columns:1fr}}}}
.pill{{padding:1px 8px;border-radius:10px;font-size:12px;background:#eef1f6}} .pill.low{{background:#e3f4ea;color:#17663a}}
.pill.medium{{background:#fff3d6;color:#7a5600}} .pill.high{{background:#fde5e3;color:#a1251b}}
.callout{{background:#f3f6fb;border-left:4px solid #3b6fd8;padding:10px 14px}}
@media print{{body{{margin:0}} .findings tr{{page-break-inside:avoid}}}}
</style></head><body>
<h1>AWS cost report</h1>
<div class="muted">Scanned {e(report['scanned_at'])} &middot; {len(report['regions_scanned'])} regions
{('&middot; account ' + e(report['account_id'])) if report.get('account_id') else ''} &middot; read-only scan, nothing was changed</div>
{sample_note}
<div class="cards">
<div class="card">Possible saving<b>{money(t['monthly_saving'])}/month</b><span class="muted">{money(t['yearly_saving'])} a year</span></div>
<div class="card">Findings<b>{t['findings']}</b><span class="muted">ranked by saving below</span></div>
{f'<div class="card">Spend last month<b>{money(spend["total_last_month"])}</b><span class="muted">{e(spend["last_month"])}</span></div>' if spend else ''}
</div>
{spend_html}
<h2>Savings, biggest first</h2>
{plan_html}
{table}
<p class="muted">Savings marked "est." use published us-east-1 list prices. EC2 prices come from the AWS Price List API.
Check each item before acting: effort is the work involved, risk is the chance of affecting something that is still needed.</p>
</body></html>"""
