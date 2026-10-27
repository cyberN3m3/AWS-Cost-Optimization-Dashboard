"""Run the scan from your own machine against any AWS account you can read.

Useful for a one-off cost review: the client creates a read-only role,
you run this with that profile, and you get the same HTML and JSON report
the weekly Lambda produces. Nothing is deployed and nothing is changed.

    python scan.py --profile client-readonly
    python scan.py --profile client-readonly --regions us-east-1 eu-west-2
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lambda'))

import boto3  # noqa: E402

import report as report_mod  # noqa: E402
from lambda_function import scan_account  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='Read-only AWS cost scan')
    parser.add_argument('--profile', help='AWS CLI profile to use')
    parser.add_argument('--regions', nargs='*', help='Limit the scan to these regions')
    parser.add_argument('--out', default='reports', help='Folder for the report files')
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile)
    account = session.client('sts').get_caller_identity()['Account']
    findings, spend, plan, regions = scan_account(session=session, regions=args.regions)

    stamp = datetime.now(timezone.utc)
    report = report_mod.build_report(findings, spend, plan, regions,
                                     stamp.strftime('%Y-%m-%d %H:%M UTC'), account)
    os.makedirs(args.out, exist_ok=True)
    base = os.path.join(args.out, f"cost-report-{account}-{stamp:%Y%m%d}")
    with open(base + '.json', 'w') as fh:
        json.dump(report, fh, indent=2, default=str)
    with open(base + '.html', 'w') as fh:
        fh.write(report_mod.render_html(report))
    print(report_mod.email_summary(report))
    print(f"\nSaved {base}.html and {base}.json")


if __name__ == '__main__':
    main()
