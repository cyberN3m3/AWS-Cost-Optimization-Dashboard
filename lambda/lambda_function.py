"""CostPatrol: a weekly, read-only scan for wasted AWS spend.

Runs on a schedule (EventBridge) or by hand with `aws lambda invoke`.
There is no public API: the report goes to a private S3 bucket and a
summary goes to email (and Slack if configured). If a run fails, the
Lambda on-failure destination sends the error to the same email topic.
"""

import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import boto3

import checks
import report as report_mod
import spend as spend_mod
from pricing import Ec2PriceBook


def enabled_regions(ec2):
    try:
        resp = ec2.describe_regions(
            Filters=[{'Name': 'opt-in-status', 'Values': ['opt-in-not-required', 'opted-in']}])
        return sorted(r['RegionName'] for r in resp['Regions'])
    except Exception as exc:
        print(f"Could not list regions, scanning us-east-1 only: {exc}")
        return ['us-east-1']


def run_check(label, func, *args):
    """One failing check (for example a missing permission) must not stop the scan."""
    try:
        return func(*args)
    except Exception as exc:
        print(f"  {label} skipped: {exc}")
        return []


def region_clients(session, region):
    """boto3 clients are thread-safe, but creating them is not, so make them before the threads start."""
    return {
        'ec2': session.client('ec2', region_name=region),
        'cw': session.client('cloudwatch', region_name=region),
        'elbv2': session.client('elbv2', region_name=region),
        'logs': session.client('logs', region_name=region),
    }


def scan_region(region, price_book, clients):
    ec2, cw, elbv2, logs = clients['ec2'], clients['cw'], clients['elbv2'], clients['logs']
    found = []
    found += run_check('idle EC2', checks.idle_ec2_instances, ec2, cw, region, price_book)
    found += run_check('unattached EBS', checks.unattached_volumes, ec2, region)
    found += run_check('gp2 volumes', checks.gp2_volumes, ec2, region)
    found += run_check('old snapshots', checks.old_snapshots, ec2, region)
    found += run_check('Elastic IPs', checks.unattached_elastic_ips, ec2, region)
    found += run_check('NAT gateways', checks.idle_nat_gateways, ec2, cw, region)
    found += run_check('load balancers', checks.idle_load_balancers, elbv2, cw, region)
    found += run_check('log groups', checks.log_groups_without_retention, logs, region)
    print(f"  {region}: {len(found)} findings")
    return found


def scan_account(session=None, home_region='us-east-1', regions=None):
    session = session or boto3.session.Session()
    price_book = Ec2PriceBook(session.client('pricing', region_name='us-east-1'))
    regions = regions or enabled_regions(session.client('ec2', region_name=home_region))
    print(f"Scanning {len(regions)} regions")
    clients = {r: region_clients(session, r) for r in regions}
    findings = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(scan_region, r, price_book, clients[r]) for r in regions]
        for future in as_completed(futures):
            findings += future.result()
    findings += run_check('S3 lifecycle', checks.buckets_without_lifecycle, session.client('s3'))

    ce = session.client('ce', region_name='us-east-1')
    spend = None
    try:
        spend = spend_mod.spend_summary(ce)
    except Exception as exc:  # Cost Explorer not enabled yet, or no permission
        print(f"Spend summary unavailable: {exc}")
    plan = spend_mod.savings_plan_recommendation(ce)
    return findings, spend, plan, regions


def save_report(s3, bucket, report, html_text):
    day = report['scanned_at'][:10]
    prefix = f"reports/{day}"
    body = json.dumps(report, indent=2, default=str)
    s3.put_object(Bucket=bucket, Key=f"{prefix}/report.json", Body=body, ContentType='application/json')
    s3.put_object(Bucket=bucket, Key=f"{prefix}/report.html", Body=html_text, ContentType='text/html')
    s3.put_object(Bucket=bucket, Key='reports/latest.json', Body=body, ContentType='application/json')
    return f"s3://{bucket}/{prefix}/report.html"


def slack_webhook_url(param_name, region):
    """The webhook is a secret, so it lives in SSM Parameter Store, not in an environment variable."""
    if not param_name:
        return ''
    try:
        ssm = boto3.client('ssm', region_name=region)
        return ssm.get_parameter(Name=param_name, WithDecryption=True)['Parameter']['Value']
    except Exception as exc:
        print(f"Could not read the Slack webhook, skipping Slack: {exc}")
        return ''


def send_slack(webhook_url, text):
    req = urllib.request.Request(
        webhook_url, data=json.dumps({'text': text}).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 - URL comes from our own config
            print(f"Slack: {resp.status}")
    except Exception as exc:
        print(f"Slack failed (report still saved): {exc}")


def lambda_handler(event, context):
    home_region = os.environ.get('AWS_REGION', 'us-east-1')
    bucket = os.environ.get('REPORT_BUCKET')
    topic_arn = os.environ.get('SNS_TOPIC_ARN')
    slack_param = os.environ.get('SLACK_WEBHOOK_PARAM', '')

    findings, spend, plan, regions = scan_account(home_region=home_region)
    account_id = ''
    try:
        account_id = boto3.client('sts').get_caller_identity()['Account']
    except Exception:
        pass

    scanned_at = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    report = report_mod.build_report(findings, spend, plan, regions, scanned_at, account_id)
    html_text = report_mod.render_html(report)

    location = ''
    if bucket:
        location = save_report(boto3.client('s3'), bucket, report, html_text)
        print(f"Report saved to {location}")

    summary = report_mod.email_summary(report, location)
    print(summary)

    if topic_arn:
        t = report['totals']
        subject = (f"[CostPatrol] {t['findings']} findings, about "
                   f"${t['monthly_saving']:,.2f}/month to save" if t['findings']
                   else "[CostPatrol] No waste found this week")
        boto3.client('sns', region_name=home_region).publish(
            TopicArn=topic_arn, Subject=subject[:100], Message=summary)
    slack_webhook = slack_webhook_url(slack_param, home_region)
    if slack_webhook:
        send_slack(slack_webhook, summary)

    return {
        'scanned_at': scanned_at,
        'findings': report['totals']['findings'],
        'monthly_saving': report['totals']['monthly_saving'],
        'report': location,
    }
