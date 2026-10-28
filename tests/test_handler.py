import json

import boto3
from moto import mock_aws

import lambda_function


@mock_aws
def test_handler_saves_report_and_emails(monkeypatch):
    s3 = boto3.client('s3', region_name='us-east-1')
    s3.create_bucket(Bucket='reports-bucket')
    topic = boto3.client('sns', region_name='us-east-1').create_topic(Name='alerts')['TopicArn']
    ec2 = boto3.client('ec2', region_name='us-east-1')
    ec2.allocate_address(Domain='vpc')

    monkeypatch.setenv('REPORT_BUCKET', 'reports-bucket')
    monkeypatch.setenv('SNS_TOPIC_ARN', topic)
    monkeypatch.setattr(lambda_function, 'enabled_regions', lambda ec2: ['us-east-1'])

    result = lambda_function.lambda_handler({}, None)

    assert result['findings'] >= 1
    assert result['report'].startswith('s3://reports-bucket/reports/')
    keys = [o['Key'] for o in s3.list_objects_v2(Bucket='reports-bucket')['Contents']]
    assert 'reports/latest.json' in keys
    saved = json.loads(s3.get_object(Bucket='reports-bucket', Key='reports/latest.json')['Body'].read())
    assert any(f['check'] == 'unattached_eip' for f in saved['findings'])


@mock_aws
def test_slack_webhook_is_read_from_parameter_store():
    boto3.client('ssm', region_name='us-east-1').put_parameter(
        Name='/costpatrol/slack', Value='https://hooks.slack.com/services/T0/B0/x', Type='SecureString')

    assert lambda_function.slack_webhook_url('/costpatrol/slack', 'us-east-1').startswith('https://hooks.slack.com/')
    assert lambda_function.slack_webhook_url('', 'us-east-1') == ''
    assert lambda_function.slack_webhook_url('/missing', 'us-east-1') == ''
