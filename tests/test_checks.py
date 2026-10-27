from datetime import datetime, timedelta, timezone

import boto3
from moto import mock_aws

import checks
from conftest import REGION


def test_smaller_type():
    assert checks.smaller_type('m5.xlarge') == 'm5.large'
    assert checks.smaller_type('t3.medium') == 't3.small'
    assert checks.smaller_type('m5.large') is None      # smallest m5 size
    assert checks.smaller_type('t3.nano') is None
    assert checks.smaller_type('weird') is None


@mock_aws
def test_unattached_volume_found_and_attached_one_ignored():
    ec2 = boto3.client('ec2', region_name=REGION)
    loose = ec2.create_volume(AvailabilityZone='us-east-1a', Size=100, VolumeType='gp3')
    image = ec2.describe_images()['Images'][0]['ImageId']
    inst = ec2.run_instances(ImageId=image, MinCount=1, MaxCount=1, InstanceType='t3.micro')
    attached = ec2.create_volume(AvailabilityZone='us-east-1a', Size=50, VolumeType='gp3')
    ec2.attach_volume(VolumeId=attached['VolumeId'], InstanceId=inst['Instances'][0]['InstanceId'],
                      Device='/dev/sdf')

    found = checks.unattached_volumes(ec2, REGION)
    ids = [f['resource_id'] for f in found]
    assert loose['VolumeId'] in ids
    assert attached['VolumeId'] not in ids
    item = next(f for f in found if f['resource_id'] == loose['VolumeId'])
    assert item['monthly_saving'] == 8.0          # 100 GB x $0.08


@mock_aws
def test_gp2_in_use_gets_gp3_saving():
    ec2 = boto3.client('ec2', region_name=REGION)
    image = ec2.describe_images()['Images'][0]['ImageId']
    inst = ec2.run_instances(ImageId=image, MinCount=1, MaxCount=1, InstanceType='t3.micro')
    vol = ec2.create_volume(AvailabilityZone='us-east-1a', Size=200, VolumeType='gp2')
    ec2.attach_volume(VolumeId=vol['VolumeId'], InstanceId=inst['Instances'][0]['InstanceId'],
                      Device='/dev/sdg')
    loose_gp2 = ec2.create_volume(AvailabilityZone='us-east-1a', Size=10, VolumeType='gp2')

    found = checks.gp2_volumes(ec2, REGION)
    ids = [f['resource_id'] for f in found]
    assert vol['VolumeId'] in ids
    assert loose_gp2['VolumeId'] not in ids       # already reported as unattached
    item = next(f for f in found if f['resource_id'] == vol['VolumeId'])
    assert item['monthly_saving'] == 4.0          # 200 GB x $0.02


@mock_aws
def test_unattached_elastic_ip():
    ec2 = boto3.client('ec2', region_name=REGION)
    free = ec2.allocate_address(Domain='vpc')
    found = checks.unattached_elastic_ips(ec2, REGION)
    assert [f['resource_id'] for f in found] == [free['AllocationId']]
    assert found[0]['monthly_saving'] == 3.65
    assert found[0]['saving_is_estimate'] is False


@mock_aws
def test_old_snapshot_reported_unless_an_ami_uses_it(later):
    ec2 = boto3.client('ec2', region_name=REGION)
    vol = ec2.create_volume(AvailabilityZone='us-east-1a', Size=40, VolumeType='gp3')
    unused = ec2.create_snapshot(VolumeId=vol['VolumeId'])
    image_id = ec2.register_image(Name='keep-me', RootDeviceName='/dev/xvda', BlockDeviceMappings=[
        {'DeviceName': '/dev/xvda', 'Ebs': {'SnapshotId': unused['SnapshotId']}}])['ImageId']
    # moto gives the AMI its own snapshot; that one must be protected
    image = ec2.describe_images(ImageIds=[image_id])['Images'][0]
    protected = image['BlockDeviceMappings'][0]['Ebs']['SnapshotId']

    assert unused['SnapshotId'] not in [f['resource_id'] for f in checks.old_snapshots(ec2, REGION)]
    later(120)
    ids = [f['resource_id'] for f in checks.old_snapshots(ec2, REGION)]
    assert unused['SnapshotId'] in ids
    assert protected not in ids


@mock_aws
def test_idle_ec2_stop_recommendation(later, price_book):
    ec2 = boto3.client('ec2', region_name=REGION)
    cw = boto3.client('cloudwatch', region_name=REGION)
    image = ec2.describe_images()['Images'][0]['ImageId']
    inst = ec2.run_instances(ImageId=image, MinCount=1, MaxCount=1, InstanceType='t3.large')
    iid = inst['Instances'][0]['InstanceId']
    future = later(2)
    for hours in (4, 28):
        cw.put_metric_data(Namespace='AWS/EC2', MetricData=[{
            'MetricName': 'CPUUtilization', 'Value': 0.4, 'Unit': 'Percent',
            'Dimensions': [{'Name': 'InstanceId', 'Value': iid}],
            'Timestamp': future - timedelta(hours=hours)}])

    found = checks.idle_ec2_instances(ec2, cw, REGION, price_book)
    assert len(found) == 1
    assert found[0]['monthly_saving'] == 60.74
    assert found[0]['action'].startswith('Stop the instance')


@mock_aws
def test_busy_ec2_is_not_idle(later, price_book):
    ec2 = boto3.client('ec2', region_name=REGION)
    cw = boto3.client('cloudwatch', region_name=REGION)
    image = ec2.describe_images()['Images'][0]['ImageId']
    iid = ec2.run_instances(ImageId=image, MinCount=1, MaxCount=1,
                            InstanceType='t3.large')['Instances'][0]['InstanceId']
    future = later(2)
    cw.put_metric_data(Namespace='AWS/EC2', MetricData=[{
        'MetricName': 'CPUUtilization', 'Value': 55.0, 'Unit': 'Percent',
        'Dimensions': [{'Name': 'InstanceId', 'Value': iid}],
        'Timestamp': future - timedelta(hours=3)}])
    assert checks.idle_ec2_instances(ec2, cw, REGION, price_book) == []


@mock_aws
def test_idle_nat_gateway():
    ec2 = boto3.client('ec2', region_name=REGION)
    cw = boto3.client('cloudwatch', region_name=REGION)
    vpc = ec2.create_vpc(CidrBlock='10.0.0.0/16')['Vpc']['VpcId']
    subnet = ec2.create_subnet(VpcId=vpc, CidrBlock='10.0.1.0/24')['Subnet']['SubnetId']
    eip = ec2.allocate_address(Domain='vpc')['AllocationId']
    nat = ec2.create_nat_gateway(SubnetId=subnet, AllocationId=eip)['NatGateway']['NatGatewayId']
    found = checks.idle_nat_gateways(ec2, cw, REGION)
    assert [f['resource_id'] for f in found] == [nat]
    assert found[0]['monthly_saving'] == 32.85


@mock_aws
def test_load_balancer_with_no_targets_is_idle():
    ec2 = boto3.client('ec2', region_name=REGION)
    elbv2 = boto3.client('elbv2', region_name=REGION)
    cw = boto3.client('cloudwatch', region_name=REGION)
    vpc = ec2.create_vpc(CidrBlock='10.1.0.0/16')['Vpc']['VpcId']
    subnets = [ec2.create_subnet(VpcId=vpc, CidrBlock=f'10.1.{i}.0/24',
                                 AvailabilityZone=f'us-east-1{az}')['Subnet']['SubnetId']
               for i, az in ((1, 'a'), (2, 'b'))]
    elbv2.create_load_balancer(Name='forgotten-alb', Subnets=subnets, Type='application')
    found = checks.idle_load_balancers(elbv2, cw, REGION)
    assert [f['resource_id'] for f in found] == ['forgotten-alb']
    assert 'no healthy targets' in found[0]['detail']


@mock_aws
def test_log_groups_without_retention():
    logs = boto3.client('logs', region_name=REGION)
    logs.create_log_group(logGroupName='/app/forever')
    logs.create_log_group(logGroupName='/app/tidy')
    logs.put_retention_policy(logGroupName='/app/tidy', retentionInDays=30)
    found = checks.log_groups_without_retention(logs, REGION)
    assert [f['resource_id'] for f in found] == ['/app/forever']


@mock_aws
def test_buckets_without_lifecycle():
    s3 = boto3.client('s3', region_name=REGION)
    s3.create_bucket(Bucket='no-rules')
    s3.create_bucket(Bucket='has-rules')
    s3.put_bucket_lifecycle_configuration(Bucket='has-rules', LifecycleConfiguration={'Rules': [
        {'ID': 'expire', 'Status': 'Enabled', 'Filter': {'Prefix': ''}, 'Expiration': {'Days': 30}}]})
    found = checks.buckets_without_lifecycle(s3)
    assert [f['resource_id'] for f in found] == ['no-rules']
