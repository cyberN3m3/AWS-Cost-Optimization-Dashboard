import checks
import report


def sample_findings():
    return [
        checks.finding('unattached_eip', 'Unattached Elastic IP', 'eipalloc-1', 'us-east-1',
                       3.65, 3.65, 'Low', 'Low', 'Release it'),
        checks.finding('idle_nat', 'Idle NAT gateway', 'nat-1', 'eu-west-2',
                       32.85, 32.85, 'Medium', 'Medium', 'Delete it'),
        checks.finding('log_retention', 'Log group', '/app/<script>', 'us-east-1',
                       0.5, 0.5, 'Low', 'Low', 'Set retention'),
    ]


def test_findings_ranked_by_saving():
    rep = report.build_report(sample_findings(), None, None, ['us-east-1'], '2026-10-27 08:00 UTC')
    assert [f['resource_id'] for f in rep['findings']] == ['nat-1', 'eipalloc-1', '/app/<script>']
    assert rep['totals']['monthly_saving'] == 37.0
    assert rep['totals']['yearly_saving'] == 444.0
    assert rep['by_check']['idle_nat']['count'] == 1


def test_html_escapes_resource_names():
    rep = report.build_report(sample_findings(), None, None, ['us-east-1'], '2026-10-27 08:00 UTC')
    page = report.render_html(rep)
    assert '<script>' not in page
    assert '&lt;script&gt;' in page
    assert 'nothing was changed' in page


def test_email_summary_lists_top_savings():
    rep = report.build_report(sample_findings(), None, {'hourly_commitment': 0.05,
                              'monthly_saving': 12.4, 'saving_pct': 18.5},
                              ['us-east-1'], '2026-10-27 08:00 UTC')
    text = report.email_summary(rep, 's3://bucket/reports/2026-10-27/report.html')
    assert '1. $32.85/month  Idle NAT gateway' in text
    assert 'Savings Plan' in text
    assert 'Full report: s3://bucket' in text


def test_empty_report():
    rep = report.build_report([], None, None, ['us-east-1'], '2026-10-27 08:00 UTC')
    assert 'No waste found' in report.email_summary(rep)
    assert 'No waste found' in report.render_html(rep)
