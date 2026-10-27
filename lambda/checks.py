"""Waste checks. Every check is read-only and returns a list of findings.

A finding is a plain dict with the same keys for every check, so the report
can rank them all together by monthly saving:

    check, title, resource_id, resource_name, region,
    monthly_cost, monthly_saving, saving_is_estimate,
    effort, risk, action, detail
"""

from datetime import datetime, timedelta, timezone

import pricing

IDLE_CPU_PERCENT = 5.0
STOP_CPU_PERCENT = 1.0
SNAPSHOT_AGE_DAYS = 90
LOOKBACK_DAYS = 7
NAT_IDLE_BYTES = 1_000_000          # under 1 MB sent in a week counts as idle

SIZE_ORDER = ['nano', 'micro', 'small', 'medium', 'large', 'xlarge',
              '2xlarge', '4xlarge', '8xlarge', '12xlarge', '16xlarge', '24xlarge']
SMALLEST_SIZE = {'t': 'nano'}       # families like m, c, r start at 'large'


def utcnow():
    return datetime.now(timezone.utc)


def finding(check, title, resource_id, region, monthly_cost, monthly_saving,
            effort, risk, action, detail='', resource_name='', saving_is_estimate=True):
    return {
        'check': check,
        'title': title,
        'resource_id': resource_id,
        'resource_name': resource_name or '',
        'region': region,
        'monthly_cost': round(monthly_cost or 0, 2),
        'monthly_saving': round(monthly_saving or 0, 2),
        'saving_is_estimate': saving_is_estimate,
        'effort': effort,
        'risk': risk,
        'action': action,
        'detail': detail,
    }


def name_tag(tags):
    return next((t['Value'] for t in tags or [] if t.get('Key') == 'Name'), '')


def smaller_type(instance_type):
    """One size down in the same family, e.g. m5.xlarge -> m5.large."""
    family, _, size = instance_type.partition('.')
    if size not in SIZE_ORDER:
        return None
    index = SIZE_ORDER.index(size)
    smallest = SMALLEST_SIZE.get(family[0], 'large')
    if index <= SIZE_ORDER.index(smallest):
        return None
    return f"{family}.{SIZE_ORDER[index - 1]}"


def metric_sum_or_avg(cw, namespace, metric, dimensions, statistic, days=LOOKBACK_DAYS):
    end = utcnow()
    resp = cw.get_metric_statistics(
        Namespace=namespace, MetricName=metric, Dimensions=dimensions,
        StartTime=end - timedelta(days=days), EndTime=end,
        Period=86400, Statistics=[statistic],
    )
    points = [p[statistic] for p in resp.get('Datapoints', [])]
    if not points:
        return None
    return sum(points) / len(points) if statistic == 'Average' else sum(points)


# ---------------------------------------------------------------- EC2 -----

def idle_ec2_instances(ec2, cw, region, price_book):
    findings = []
    for page in ec2.get_paginator('describe_instances').paginate(
            Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]):
        for reservation in page['Reservations']:
            for inst in reservation['Instances']:
                launched = inst.get('LaunchTime', utcnow())
                if (utcnow() - launched).total_seconds() < 24 * 3600:
                    continue
                avg_cpu = metric_sum_or_avg(
                    cw, 'AWS/EC2', 'CPUUtilization',
                    [{'Name': 'InstanceId', 'Value': inst['InstanceId']}], 'Average')
                if avg_cpu is None or avg_cpu >= IDLE_CPU_PERCENT:
                    continue
                itype = inst['InstanceType']
                cost, source = price_book.monthly(itype, region)
                if avg_cpu < STOP_CPU_PERCENT:
                    saving, action, effort = cost, 'Stop the instance, or terminate it after taking an AMI backup', 'Low'
                else:
                    target = smaller_type(itype)
                    target_cost = price_book.monthly(target, region)[0] if target else None
                    if target and cost is not None and target_cost is not None:
                        saving, action = cost - target_cost, f'Downsize to {target}'
                    else:
                        saving, action = 0, 'Review the workload and right-size it by hand'
                    effort = 'Medium'
                findings.append(finding(
                    'idle_ec2', 'Idle EC2 instance', inst['InstanceId'], region,
                    cost, saving, effort, 'Medium', action,
                    detail=f'{itype}, average CPU {avg_cpu:.2f}% over {LOOKBACK_DAYS} days'
                           + ('' if source == 'price-list' else ' (price estimated)'),
                    resource_name=name_tag(inst.get('Tags')),
                    saving_is_estimate=source != 'price-list'))
    return findings


# ---------------------------------------------------------------- EBS -----

def unattached_volumes(ec2, region):
    findings = []
    for page in ec2.get_paginator('describe_volumes').paginate(
            Filters=[{'Name': 'status', 'Values': ['available']}]):
        for vol in page['Volumes']:
            cost = pricing.ebs_monthly(vol['Size'], vol['VolumeType'])
            findings.append(finding(
                'unattached_ebs', 'Unattached EBS volume', vol['VolumeId'], region,
                cost, cost, 'Low', 'Low', 'Take a snapshot, then delete the volume',
                detail=f"{vol['Size']} GB {vol['VolumeType']}, created {vol['CreateTime']:%Y-%m-%d}",
                resource_name=name_tag(vol.get('Tags'))))
    return findings


def gp2_volumes(ec2, region):
    findings = []
    for page in ec2.get_paginator('describe_volumes').paginate(
            Filters=[{'Name': 'volume-type', 'Values': ['gp2']},
                     {'Name': 'status', 'Values': ['in-use']}]):  # unattached ones are reported above
        for vol in page['Volumes']:
            cost = pricing.ebs_monthly(vol['Size'], 'gp2')
            saving = cost - pricing.ebs_monthly(vol['Size'], 'gp3')
            findings.append(finding(
                'gp2_to_gp3', 'gp2 volume that can move to gp3', vol['VolumeId'], region,
                cost, saving, 'Low', 'Low',
                'Change the volume type to gp3 (no downtime, about 20% cheaper)',
                detail=f"{vol['Size']} GB gp2, state {vol['State']}",
                resource_name=name_tag(vol.get('Tags'))))
    return findings


def old_snapshots(ec2, region, account_id='self'):
    """Snapshots older than 90 days that no AMI depends on."""
    in_use = set()
    try:
        for image in ec2.describe_images(Owners=['self'])['Images']:
            for mapping in image.get('BlockDeviceMappings', []):
                snap = mapping.get('Ebs', {}).get('SnapshotId')
                if snap:
                    in_use.add(snap)
    except Exception as exc:
        print(f"describe_images failed in {region}: {exc}")
    cutoff = utcnow() - timedelta(days=SNAPSHOT_AGE_DAYS)
    findings = []
    for page in ec2.get_paginator('describe_snapshots').paginate(OwnerIds=[account_id]):
        for snap in page['Snapshots']:
            if snap['StartTime'] > cutoff or snap['SnapshotId'] in in_use:
                continue
            # Snapshots are incremental, so the volume size is the most it can cost.
            cost = round(snap.get('VolumeSize', 0) * pricing.SNAPSHOT_GB_MONTH, 2)
            age = (utcnow() - snap['StartTime']).days
            findings.append(finding(
                'old_snapshot', 'Old EBS snapshot', snap['SnapshotId'], region,
                cost, cost, 'Low', 'Medium',
                'Confirm it is not needed for recovery, then delete it or move it to the archive tier',
                detail=f"{snap.get('VolumeSize', 0)} GB, {age} days old (cost shown is the most it can be)",
                resource_name=name_tag(snap.get('Tags'))))
    return findings


# ------------------------------------------------------------ Network -----

def unattached_elastic_ips(ec2, region):
    cost = round(pricing.PUBLIC_IPV4_HOUR * pricing.HOURS_PER_MONTH, 2)
    findings = []
    for addr in ec2.describe_addresses().get('Addresses', []):
        if addr.get('AssociationId') or addr.get('InstanceId') or addr.get('NetworkInterfaceId'):
            continue
        findings.append(finding(
            'unattached_eip', 'Unattached Elastic IP', addr.get('AllocationId', addr.get('PublicIp')),
            region, cost, cost, 'Low', 'Low',
            'Release the address if nothing needs this exact IP',
            detail=f"{addr.get('PublicIp')} is not attached; AWS charges for every public IPv4 address",
            resource_name=name_tag(addr.get('Tags')), saving_is_estimate=False))
    return findings


def idle_nat_gateways(ec2, cw, region):
    cost = round(pricing.NAT_GATEWAY_HOUR * pricing.HOURS_PER_MONTH, 2)
    findings = []
    for page in ec2.get_paginator('describe_nat_gateways').paginate(
            Filters=[{'Name': 'state', 'Values': ['available']}]):
        for nat in page['NatGateways']:
            sent = metric_sum_or_avg(
                cw, 'AWS/NATGateway', 'BytesOutToDestination',
                [{'Name': 'NatGatewayId', 'Value': nat['NatGatewayId']}], 'Sum')
            if sent is not None and sent >= NAT_IDLE_BYTES:
                continue
            findings.append(finding(
                'idle_nat', 'Idle NAT gateway', nat['NatGatewayId'], region,
                cost, cost, 'Medium', 'Medium',
                'Check the route tables that use it, then delete it (or replace it with VPC endpoints)',
                detail=f"Sent {int(sent or 0):,} bytes in {LOOKBACK_DAYS} days, in {nat.get('VpcId')}",
                resource_name=name_tag(nat.get('Tags'))))
    return findings


def idle_load_balancers(elbv2, cw, region):
    cost = round(pricing.LOAD_BALANCER_HOUR * pricing.HOURS_PER_MONTH, 2)
    findings = []
    for page in elbv2.get_paginator('describe_load_balancers').paginate():
        for lb in page['LoadBalancers']:
            arn = lb['LoadBalancerArn']
            lb_type = lb.get('Type', 'application')
            if lb_type not in ('application', 'network'):
                continue
            healthy = 0
            try:
                groups = elbv2.describe_target_groups(LoadBalancerArn=arn).get('TargetGroups', [])
            except Exception as exc:  # a load balancer with no target groups at all
                if 'TargetGroupNotFound' not in str(exc):
                    raise
                groups = []
            for tg in groups:
                health = elbv2.describe_target_health(TargetGroupArn=tg['TargetGroupArn'])
                healthy += sum(1 for t in health.get('TargetHealthDescriptions', [])
                               if t.get('TargetHealth', {}).get('State') == 'healthy')
            dimension = arn.split(':loadbalancer/')[-1]
            namespace, metric = (('AWS/ApplicationELB', 'RequestCount') if lb_type == 'application'
                                 else ('AWS/NetworkELB', 'NewFlowCount'))
            traffic = metric_sum_or_avg(cw, namespace, metric,
                                        [{'Name': 'LoadBalancer', 'Value': dimension}], 'Sum')
            if healthy > 0 and traffic:
                continue
            reason = 'no healthy targets' if healthy == 0 else f'no traffic in {LOOKBACK_DAYS} days'
            findings.append(finding(
                'idle_load_balancer', f'Idle {lb_type} load balancer', lb['LoadBalancerName'], region,
                cost, cost, 'Low', 'Medium',
                'Confirm nothing points at it (DNS, other services), then delete it',
                detail=f'{reason}; cost shown is the hourly charge only'))
    return findings


# --------------------------------------------------------------- Logs -----

def log_groups_without_retention(logs, region):
    findings = []
    for page in logs.get_paginator('describe_log_groups').paginate():
        for group in page['logGroups']:
            if group.get('retentionInDays'):
                continue
            stored_gb = group.get('storedBytes', 0) / 1_000_000_000
            cost = round(stored_gb * pricing.LOG_STORAGE_GB_MONTH, 2)
            findings.append(finding(
                'log_retention', 'Log group that keeps data forever', group['logGroupName'], region,
                cost, cost, 'Low', 'Low',
                'Set a retention period (for example 30 or 90 days)',
                detail=f'{stored_gb:.2f} GB stored with no expiry; saving shown is the most it can be'))
    return findings


# ----------------------------------------------------------------- S3 -----

def buckets_without_lifecycle(s3):
    findings = []
    for bucket in s3.list_buckets().get('Buckets', []):
        name = bucket['Name']
        try:
            s3.get_bucket_lifecycle_configuration(Bucket=name)
        except Exception as exc:
            code = getattr(exc, 'response', {}).get('Error', {}).get('Code', '')
            if code != 'NoSuchLifecycleConfiguration':
                continue
            findings.append(finding(
                's3_no_lifecycle', 'S3 bucket with no lifecycle rules', name, 'global',
                0, 0, 'Low', 'Low',
                'Add lifecycle rules: move old objects to cheaper storage and expire what is not needed',
                detail='Storage grows with no limit; the saving depends on how much data is old'))
    return findings
