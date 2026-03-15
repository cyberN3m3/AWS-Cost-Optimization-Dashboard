import boto3
import json
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed



def get_enabled_regions(ec2_client):
    try:
        response = ec2_client.describe_regions(
            Filters=[{'Name': 'opt-in-status', 'Values': ['opt-in-not-required', 'opted-in']}]
        )
        return [r['RegionName'] for r in response['Regions']]
    except Exception as e:
        print(f"Could not fetch regions, defaulting to us-east-1: {e}")
        return ['us-east-1']



# RIGHT-SIZING TABLE

DOWNSIZE_MAP = {
    't2.medium':   't2.small',    't2.large':    't2.medium',
    't2.xlarge':   't2.large',    't2.2xlarge':  't2.xlarge',
    't3.small':    't3.nano',     't3.medium':   't3.small',
    't3.large':    't3.medium',   't3.xlarge':   't3.large',
    't3.2xlarge':  't3.xlarge',
    't3a.small':   't3a.nano',    't3a.medium':  't3a.small',
    't3a.large':   't3a.medium',  't3a.xlarge':  't3a.large',
    'm5.large':    't3.large',    'm5.xlarge':   'm5.large',
    'm5.2xlarge':  'm5.xlarge',   'm5.4xlarge':  'm5.2xlarge',
    'm6i.large':   't3.large',    'm6i.xlarge':  'm6i.large',
    'm6i.2xlarge': 'm6i.xlarge',
    'c5.large':    't3.large',    'c5.xlarge':   'c5.large',
    'c5.2xlarge':  'c5.xlarge',   'c5.4xlarge':  'c5.2xlarge',
    'r5.large':    'm5.large',    'r5.xlarge':   'r5.large',
    'r5.2xlarge':  'r5.xlarge',
}

EC2_PRICES = {
    't2.nano': 2.11,    't2.micro': 4.23,    't2.small': 8.47,
    't2.medium': 16.94, 't2.large': 33.87,   't2.xlarge': 67.74,
    't3.nano': 1.89,    't3.micro': 3.80,    't3.small': 7.59,
    't3.medium': 15.18, 't3.large': 30.37,   't3.xlarge': 60.74,
    't3.2xlarge': 121.47,
    't3a.nano': 1.69,   't3a.micro': 3.38,   't3a.small': 6.77,
    't3a.medium': 13.54,'t3a.large': 27.07,  't3a.xlarge': 54.14,
    'm5.large': 69.12,  'm5.xlarge': 138.24, 'm5.2xlarge': 276.48,
    'm5.4xlarge': 552.96,
    'm6i.large': 69.12, 'm6i.xlarge': 138.24,'m6i.2xlarge': 276.48,
    'c5.large': 62.05,  'c5.xlarge': 124.10, 'c5.2xlarge': 248.19,
    'c5.4xlarge': 496.38,
    'r5.large': 90.52,  'r5.xlarge': 181.04, 'r5.2xlarge': 362.08,
}



def get_rightsizing_recommendation(instance_type: str, avg_cpu: float) -> dict:
    current_cost = EC2_PRICES.get(instance_type, 75.00)

    if avg_cpu < 1.0:
        return {
            'recommendation':  'Stop instance',
            'reason':          f'CPU averaged {avg_cpu}% — no meaningful workload detected',
            'suggested_type':  None,
            'monthly_savings': current_cost,
        }

    suggested = DOWNSIZE_MAP.get(instance_type)
    if suggested:
        suggested_cost = EC2_PRICES.get(suggested, current_cost * 0.5)
        savings = round(current_cost - suggested_cost, 2)
        return {
            'recommendation':  f'Downsize to {suggested}',
            'reason':          f'CPU averaged {avg_cpu}% — workload fits a smaller instance',
            'suggested_type':  suggested,
            'monthly_savings': savings,
        }

    return {
        'recommendation':  'Review and right-size manually',
        'reason':          f'No automated downsize path for {instance_type}',
        'suggested_type':  None,
        'monthly_savings': 0,
    }



# EC2 SCANNER

def get_idle_ec2_instances(ec2_client, cw_client, region):
    idle = []
    paginator = ec2_client.get_paginator('describe_instances')

    for page in paginator.paginate(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
    ):
        for reservation in page['Reservations']:
            for inst in reservation['Instances']:
                instance_id   = inst['InstanceId']
                instance_type = inst['InstanceType']
                launch_time   = inst.get('LaunchTime', datetime.utcnow())

                age_hours = (
                    datetime.utcnow().replace(tzinfo=None) -
                    launch_time.replace(tzinfo=None)
                ).total_seconds() / 3600
                if age_hours < 24:
                    continue

                name = next(
                    (t['Value'] for t in inst.get('Tags', []) if t['Key'] == 'Name'),
                    'Unnamed'
                )

                response = cw_client.get_metric_statistics(
                    Namespace='AWS/EC2',
                    MetricName='CPUUtilization',
                    Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
                    StartTime=datetime.utcnow() - timedelta(days=7),
                    EndTime=datetime.utcnow(),
                    Period=86400,
                    Statistics=['Average']
                )
                datapoints = response.get('Datapoints', [])
                avg_cpu = (
                    sum(d['Average'] for d in datapoints) / len(datapoints)
                ) if datapoints else 0.0

                if avg_cpu < 5.0:
                    rightsizing = get_rightsizing_recommendation(instance_type, avg_cpu)
                    idle.append({
                        'InstanceId':   instance_id,
                        'Name':         name,
                        'InstanceType': instance_type,
                        'AvgCPU7d':     round(avg_cpu, 2),
                        'Region':       region,
                        'MonthlyCost':  EC2_PRICES.get(instance_type, 75.00),
                        'Action':       rightsizing['recommendation'],
                        'Rightsizing':  rightsizing,
                    })

    return idle



# EBS SCANNER

def get_unattached_ebs_volumes(ec2_client, region):
    unattached = []
    paginator = ec2_client.get_paginator('describe_volumes')

    for page in paginator.paginate(
        Filters=[{'Name': 'status', 'Values': ['available']}]
    ):
        for vol in page['Volumes']:
            size_gb  = vol['Size']
            vol_type = vol['VolumeType']
            name = next(
                (t['Value'] for t in vol.get('Tags', []) if t['Key'] == 'Name'),
                'Unnamed'
            )
            unattached.append({
                'VolumeId':    vol['VolumeId'],
                'Name':        name,
                'SizeGB':      size_gb,
                'VolumeType':  vol_type,
                'CreatedAt':   vol['CreateTime'].strftime('%Y-%m-%d'),
                'Region':      region,
                'MonthlyCost': _ebs_monthly_cost(size_gb, vol_type),
                'Action':      'Snapshot then delete',
            })

    return unattached



# S3 SCANNER

def get_s3_buckets_without_lifecycle(s3_client):
    no_lifecycle = []
    response = s3_client.list_buckets()

    for bucket in response.get('Buckets', []):
        bucket_name = bucket['Name']
        try:
            s3_client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
        except Exception as e:
            error_code = ''
            if hasattr(e, 'response'):
                error_code = e.response.get('Error', {}).get('Code', '')
            if error_code == 'NoSuchLifecycleConfiguration':
                no_lifecycle.append({
                    'BucketName':  bucket_name,
                    'MonthlyCost': 0,
                    'Action':      'Add lifecycle: transition to Glacier after 90 days, expire after 365',
                })

    return no_lifecycle



# RESERVED INSTANCE ANALYSER

def get_reserved_instance_opportunities(ec2_client, region):
    opportunities = []
    paginator = ec2_client.get_paginator('describe_instances')

    existing_ri_types = set()
    try:
        ri_response = ec2_client.describe_reserved_instances(
            Filters=[{'Name': 'state', 'Values': ['active']}]
        )
        for ri in ri_response.get('ReservedInstances', []):
            existing_ri_types.add(ri['InstanceType'])
    except Exception:
        pass

    for page in paginator.paginate(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
    ):
        for reservation in page['Reservations']:
            for inst in reservation['Instances']:
                instance_type = inst['InstanceType']
                launch_time   = inst.get('LaunchTime', datetime.utcnow())

                if instance_type in existing_ri_types:
                    continue

                age_days = (
                    datetime.utcnow().replace(tzinfo=None) -
                    launch_time.replace(tzinfo=None)
                ).days

                if age_days < 30:
                    continue

                name = next(
                    (t['Value'] for t in inst.get('Tags', []) if t['Key'] == 'Name'),
                    'Unnamed'
                )

                on_demand_monthly = EC2_PRICES.get(instance_type, 75.00)
                ri_monthly        = round(on_demand_monthly * 0.68, 2)
                monthly_savings   = round(on_demand_monthly - ri_monthly, 2)
                annual_savings    = round(monthly_savings * 12, 2)

                opportunities.append({
                    'InstanceId':       inst['InstanceId'],
                    'Name':             name,
                    'InstanceType':     instance_type,
                    'RunningDays':      age_days,
                    'Region':           region,
                    'OnDemandMonthly':  on_demand_monthly,
                    'RIMonthly':        ri_monthly,
                    'MonthlySavings':   monthly_savings,
                    'AnnualSavings':    annual_savings,
                    'Action':           f'Purchase 1-year No Upfront RI for {instance_type} — save ${annual_savings}/year',
                })

    return opportunities



# COST HELPERS

def _ebs_monthly_cost(size_gb: int, vol_type: str) -> float:
    prices = {
        'gp2': 0.10, 'gp3': 0.08, 'io1': 0.125,
        'io2': 0.125, 'st1': 0.045, 'sc1': 0.025, 'standard': 0.05
    }
    return round(size_gb * prices.get(vol_type, 0.10), 2)



# SLACK NOTIFIER

def send_slack_notification(webhook_url: str, summary: dict):
    import urllib.request

    total      = summary['total_monthly_waste_usd']
    ri_savings = summary['total_ri_annual_savings']
    ec2_count  = len(summary['idle_ec2'])
    ebs_count  = len(summary['unattached_ebs'])
    s3_count   = len(summary['s3_no_lifecycle'])
    ri_count   = len(summary['ri_opportunities'])
    regions    = len(summary['regions_scanned'])

    color = '#36a64f' if total == 0 else '#D4500E'

    payload = {
        "attachments": [{
            "color": color,
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "☁️ AWS Weekly Cost Report"}
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Monthly waste*\n${total:.2f}"},
                        {"type": "mrkdwn", "text": f"*Annual waste*\n${total * 12:.2f}"},
                        {"type": "mrkdwn", "text": f"*RI savings available*\n${ri_savings:.2f}/yr"},
                        {"type": "mrkdwn", "text": f"*Regions scanned*\n{regions}"},
                    ]
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"• *Idle EC2 instances:* {ec2_count}\n"
                            f"• *Unattached EBS volumes:* {ebs_count}\n"
                            f"• *S3 without lifecycle rules:* {s3_count}\n"
                            f"• *Reserved Instance opportunities:* {ri_count}"
                        )
                    }
                },
                {
                    "type": "context",
                    "elements": [{
                        "type": "mrkdwn",
                        "text": f"Scanned at {summary['scanned_at']} · aws-cost-optimizer v2.0"
                    }]
                }
            ]
        }]
    }

    data = json.dumps(payload).encode('utf-8')
    req  = urllib.request.Request(
        webhook_url,
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"Slack notification sent: {resp.status}")
    except Exception as e:
        print(f"Slack notification failed (non-fatal): {e}")



# REPORT GENERATOR

def generate_report(idle_ec2, unattached_ebs, s3_no_lifecycle,
                    ri_opportunities, regions_scanned):

    ec2_waste  = sum(i['MonthlyCost'] for i in idle_ec2)
    ebs_waste  = sum(v['MonthlyCost'] for v in unattached_ebs)
    total      = ec2_waste + ebs_waste
    ri_savings = sum(r['AnnualSavings'] for r in ri_opportunities)

    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    sep = '=' * 62

    lines = [
        sep,
        '  AWS COST OPTIMIZATION REPORT  v2.0',
        f'  Generated: {now}',
        f'  Regions scanned: {", ".join(regions_scanned)}',
        sep, '',
        f'  ESTIMATED MONTHLY WASTE:     ${total:>8.2f}',
        f'  ESTIMATED ANNUAL WASTE:      ${total * 12:>8.2f}',
        f'  AVAILABLE RI SAVINGS:        ${ri_savings:>8.2f}/year',
        '',
    ]

    lines += [
        '-' * 62,
        f'  IDLE EC2 INSTANCES  ({len(idle_ec2)} found | ~${ec2_waste:.2f}/month)',
        '-' * 62,
    ]
    if idle_ec2:
        for i in idle_ec2:
            rs = i.get('Rightsizing', {})
            lines += [
                f"  * {i['Name']} ({i['InstanceId']}) [{i['Region']}]",
                f"    Type: {i['InstanceType']} | Avg CPU (7d): {i['AvgCPU7d']}%",
                f"    Current cost: ~${i['MonthlyCost']:.2f}/month",
                f"    Recommendation: {rs.get('recommendation', i['Action'])}",
                f"    Potential savings: ~${rs.get('monthly_savings', 0):.2f}/month", '',
            ]
    else:
        lines += ['  No idle EC2 instances found.', '']

    lines += [
        '-' * 62,
        f'  UNATTACHED EBS VOLUMES  ({len(unattached_ebs)} found | ~${ebs_waste:.2f}/month)',
        '-' * 62,
    ]
    if unattached_ebs:
        for v in unattached_ebs:
            lines += [
                f"  * {v['VolumeId']} ({v['Name']}) [{v['Region']}]",
                f"    {v['SizeGB']} GB {v['VolumeType']} | Created: {v['CreatedAt']}",
                f"    Cost: ~${v['MonthlyCost']:.2f}/month | Action: {v['Action']}", '',
            ]
    else:
        lines += ['  No unattached EBS volumes found.', '']

    lines += [
        '-' * 62,
        f'  S3 BUCKETS WITHOUT LIFECYCLE RULES  ({len(s3_no_lifecycle)} found)',
        '-' * 62,
    ]
    if s3_no_lifecycle:
        for b in s3_no_lifecycle:
            lines += [f"  * {b['BucketName']}", f"    Action: {b['Action']}", '']
    else:
        lines += ['  All S3 buckets have lifecycle rules configured.', '']

    lines += [
        '-' * 62,
        f'  RESERVED INSTANCE OPPORTUNITIES  ({len(ri_opportunities)} found | save ${ri_savings:.2f}/year)',
        '-' * 62,
    ]
    if ri_opportunities:
        for r in ri_opportunities:
            lines += [
                f"  * {r['Name']} ({r['InstanceId']}) [{r['Region']}]",
                f"    Type: {r['InstanceType']} | Running: {r['RunningDays']} days",
                f"    On-demand: ${r['OnDemandMonthly']:.2f}/month → RI: ${r['RIMonthly']:.2f}/month",
                f"    Action: {r['Action']}", '',
            ]
    else:
        lines += ['  No Reserved Instance opportunities found.', '']

    lines += [
        sep, '  SUMMARY', sep,
        f"  Idle EC2 instances:          {len(idle_ec2):>3}  | ${ec2_waste:>8.2f}/month waste",
        f"  Unattached EBS volumes:      {len(unattached_ebs):>3}  | ${ebs_waste:>8.2f}/month waste",
        f"  S3 without lifecycle rules:  {len(s3_no_lifecycle):>3}  | review needed",
        f"  RI opportunities:            {len(ri_opportunities):>3}  | ${ri_savings:>8.2f}/year savings",
        f"  {'─' * 44}",
        f"  TOTAL MONTHLY WASTE:              ${total:>8.2f}/month",
        f"  TOTAL ANNUAL WASTE:               ${total * 12:>8.2f}/year",
        f"  POTENTIAL RI SAVINGS:             ${ri_savings:>8.2f}/year",
        '',
        f'  Regions scanned ({len(regions_scanned)}): {", ".join(regions_scanned)}',
        '  Report by: AWS Cost Optimizer v2.0',
        sep,
    ]

    return '\n'.join(lines), total



# CONCURRENT REGION SCANNER

def scan_region(region):
    try:
        ec2 = boto3.client('ec2',        region_name=region)
        cw  = boto3.client('cloudwatch', region_name=region)

        region_ec2 = get_idle_ec2_instances(ec2, cw, region)
        region_ebs = get_unattached_ebs_volumes(ec2, region)
        region_ri  = get_reserved_instance_opportunities(ec2, region)

        print(
            f"     {region}: EC2={len(region_ec2)} idle | "
            f"EBS={len(region_ebs)} unattached | "
            f"RI={len(region_ri)} opps"
        )
        return {
            'idle_ec2':       region_ec2,
            'unattached_ebs': region_ebs,
            'ri_opps':        region_ri,
        }
    except Exception as e:
        print(f"     Skipping {region} — {e}")
        return {'idle_ec2': [], 'unattached_ebs': [], 'ri_opps': []}



# LAMBDA ENTRY POINT

def lambda_handler(event, context):
    home_region   = os.environ.get('AWS_REGION', 'us-east-1')
    topic_arn     = os.environ.get('SNS_TOPIC_ARN')
    slack_webhook = os.environ.get('SLACK_WEBHOOK_URL', '')

    home_ec2    = boto3.client('ec2', region_name=home_region)
    all_regions = get_enabled_regions(home_ec2)
    print(f"Scanning {len(all_regions)} regions concurrently: {', '.join(all_regions)}")

    all_idle_ec2       = []
    all_unattached_ebs = []
    all_ri_opps        = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(scan_region, region): region
            for region in all_regions
        }
        for future in as_completed(futures):
            result = future.result()
            all_idle_ec2       += result['idle_ec2']
            all_unattached_ebs += result['unattached_ebs']
            all_ri_opps        += result['ri_opps']

    print("  → Scanning S3 (global)...")
    s3 = boto3.client('s3')
    s3_no_lifecycle = get_s3_buckets_without_lifecycle(s3)

    report, total_waste = generate_report(
        all_idle_ec2, all_unattached_ebs,
        s3_no_lifecycle, all_ri_opps,
        all_regions
    )
    print(report)

    ec2_waste  = sum(i['MonthlyCost'] for i in all_idle_ec2)
    ebs_waste  = sum(v['MonthlyCost'] for v in all_unattached_ebs)
    ri_savings = sum(r['AnnualSavings'] for r in all_ri_opps)

    if topic_arn:
        has_findings = total_waste > 0 or ri_savings > 0
        subject = (
            f"[AWS Cost Report] ${total_waste:.2f}/month waste + "
            f"${ri_savings:.2f}/yr RI savings found"
            if has_findings else
            "[AWS Cost Report] Account is clean — no waste detected"
        )
        sns = boto3.client('sns', region_name=home_region)
        sns.publish(TopicArn=topic_arn, Subject=subject, Message=report)
        print(f"SNS email sent to {topic_arn}")

    summary = {
        'scanned_at':              datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
        'regions_scanned':         all_regions,
        'total_monthly_waste_usd': round(total_waste, 2),
        'total_ri_annual_savings': round(ri_savings, 2),
        'idle_ec2':                all_idle_ec2,
        'unattached_ebs':          all_unattached_ebs,
        's3_no_lifecycle':         s3_no_lifecycle,
        'ri_opportunities':        all_ri_opps,
    }

    if slack_webhook:
        send_slack_notification(slack_webhook, summary)

    def serialise(obj):
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serialisable")

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type":                 "application/json",
            "Access-Control-Allow-Origin":  "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "body": json.dumps(summary, default=serialise),
    }