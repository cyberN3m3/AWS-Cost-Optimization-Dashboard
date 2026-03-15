# AWS Cost Optimization Dashboard

<div align="center">

**Serverless AWS spend intelligence — finds idle compute, orphaned storage, and misconfigured S3 across all regions. Delivers weekly reports via email and Slack. Costs $0.00/month to operate.**

![Python](https://img.shields.io/badge/Python-3.12-blue?style=flat-square&logo=python&logoColor=white)
![Terraform](https://img.shields.io/badge/Terraform-≥1.5-purple?style=flat-square&logo=terraform&logoColor=white)
![AWS Lambda](https://img.shields.io/badge/AWS-Lambda-orange?style=flat-square&logo=amazonaws&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)
![Cost](https://img.shields.io/badge/Monthly%20Cost-$0.00-brightgreen?style=flat-square)

</div>

---

## Overview

Every AWS account accumulates ghost resources — servers provisioned for a test, storage volumes left after instance termination, S3 buckets accumulating logs with no expiry policy. AWS does not alert you. It bills you.

This tool scans all enabled AWS regions concurrently, identifies the four most common sources of hidden waste, and delivers a formatted cost report to your inbox every Monday — with a live web dashboard for on-demand scans.

**Live finding on a real account (March 2026):**
> Identified **$45.60 in annual waste** — a t3.micro instance running at 0.56% average CPU for 7 consecutive days with no justifiable workload.

---

## What It Detects

| Category | Detection Logic | Typical Monthly Cost |
|---|---|---|
| **Idle EC2 instances** | Running instances with < 5% average CPU over 7 days | $30–$300 per instance |
| **Unattached EBS volumes** | Volumes in `available` state — not attached to any instance | $0.08–$0.125/GB/month |
| **S3 without lifecycle rules** | Buckets with no automated expiry or storage-class transition | Grows to Standard at $0.023/GB forever |
| **Reserved Instance gaps** | On-demand instances running 30+ days without RI coverage | 32–40% overpay vs 1-year No Upfront RI |

Each EC2 finding includes a **right-sizing recommendation** — not just "this is idle" but "stop it and save $270/month" or "downsize from m5.large to t3.large and save $39/month."

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│         EventBridge — cron(0 8 ? * MON *)                   │
│         Every Monday at 08:00 UTC                           │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              AWS Lambda  (Python 3.12 · 256MB)              │
│                                                             │
│   describe_regions() → all enabled regions                  │
│                                                             │
│   ThreadPoolExecutor (max_workers=10)                       │
│   ├── EC2 Scanner + CloudWatch CPU metrics  ─┐              │
│   ├── EBS Volume Scanner                     ├─ per region  │
│   └── RI Coverage Analyser                  ─┘              │
│                                                             │
│   S3 Scanner (global — runs once)                           │
│   Right-sizing Engine                                       │
│   Cost Report Generator                                     │
└────────────┬────────────────────────────┬───────────────────┘
             │                            │
      SNS Publish                   API Response
             │                            │
   ┌─────────────────┐      ┌─────────────────────────┐
   │  Email (weekly) │      │  API Gateway HTTP        │
   │  Slack webhook  │      │  POST /scan              │
   └─────────────────┘      └──────────┬──────────────┘
                                       │
                            ┌──────────────────────┐
                            │  index.html           │
                            │  S3 static website    │
                            │  Live dashboard       │
                            └──────────────────────┘
```

**IAM Role — read-only, least-privilege:**

| Permission | Scope | Purpose |
|---|---|---|
| `ec2:DescribeInstances` | `*` (AWS requirement for Describe calls) | List running instances |
| `ec2:DescribeVolumes` | `*` | Find unattached EBS volumes |
| `ec2:DescribeRegions` | `*` | Discover all enabled regions |
| `ec2:DescribeReservedInstances` | `*` | Check existing RI coverage |
| `s3:ListAllMyBuckets` | `*` | List all S3 buckets |
| `s3:GetBucketLifecycleConfiguration` | `*` | Check lifecycle policies |
| `cloudwatch:GetMetricStatistics` | `*` | Query CPU utilisation data |
| `sns:Publish` | **Specific topic ARN only** | Send cost report — scoped, not wildcard |
| `logs:CreateLogStream`, `logs:PutLogEvents` | `arn:aws:logs:*:*:*` | Lambda execution logs |

---

## Security

**This section is important. Read before deploying on any account.**

### The role is read-only

The Lambda execution role has no `Delete`, `Stop`, `Terminate`, `Create`, or `Modify` permissions anywhere in its policy. It cannot change anything in your account. Provide the IAM policy JSON from `terraform/main.tf` to any client's security team for review before deploying.

### No credentials in code

No AWS credentials, access keys, or secrets exist anywhere in this repository. The Lambda function authenticates using its IAM execution role, which is managed automatically by AWS. Terraform uses your locally configured CLI credentials, which never touch the deployed infrastructure.

### What data leaves your account

| Destination | Data transmitted | When |
|---|---|---|
| SNS → your email | Formatted cost report (resource IDs, costs, recommendations) | Every Monday 08:00 UTC |
| Slack webhook (optional) | Summary card — findings count and total waste | Same as above |
| API Gateway → your browser | Full scan JSON payload | When you click Run Scan |
| Nowhere else | — | Never |

No data is sent to any third-party service. No telemetry. No analytics. No external APIs beyond AWS and optionally Slack.

### Files that must never be committed

```
terraform/terraform.tfvars          # Contains your email address
terraform/terraform.tfvars.example  # SAFE to commit — no real values
terraform/*.tfstate                 # Contains real AWS resource IDs and ARNs
terraform/*.tfstate.backup          # Same as above
terraform/.terraform/               # Provider binaries — regenerated on init
```

The `.gitignore` in this repository excludes all of these. Run `git status` before every push to verify no sensitive files are staged.

### Dashboard hosting (public S3)

The `index.html` dashboard is hosted on a public S3 bucket. Anyone with the URL can load the dashboard and trigger a scan. The scan itself is read-only — the worst-case impact of an unauthorised invocation is a small increase in Lambda cost (fractions of a cent per scan). If you need to restrict access, add CloudFront with signed URLs or an API key on the API Gateway route.

### IAM user for Terraform (recommended)

Do not use your root account or personal admin credentials for deployment. Create a dedicated IAM user with only the permissions Terraform needs to create and manage these specific resources, then delete or deactivate the credentials after deployment:

```bash
# After terraform apply succeeds, the deployed Lambda uses its own role
# Your personal credentials are no longer needed until you run terraform destroy
aws iam delete-access-key --access-key-id YOUR_KEY_ID --user-name terraform-deployer
```

---

## Prerequisites

```bash
# AWS CLI — macOS
brew install awscli

# AWS CLI — Windows
# https://awscli.amazonaws.com/AWSCLIV2.msi

# Terraform — macOS
brew tap hashicorp/tap && brew install hashicorp/tap/terraform

# Terraform — Windows
# https://developer.hashicorp.com/terraform/downloads

# Verify installations
aws --version        # aws-cli/2.x.x
terraform -version   # Terraform v1.x.x

# Configure credentials (use a non-root IAM user)
aws configure
```

---

## Deployment

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_USERNAME/aws-cost-optimizer
cd aws-cost-optimizer/terraform

cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
aws_region        = "us-east-1"
project_name      = "aws-cost-optimizer"
alert_email       = "your-email@example.com"
slack_webhook_url = ""   # Optional — leave empty to disable Slack
```

### 2. Deploy

```bash
terraform init
terraform plan    # Review what will be created — always read this before apply
terraform apply   # Type "yes" to confirm
```

Terraform creates 11 AWS resources in approximately 45 seconds and prints:

```
api_endpoint         = "https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/scan"
lambda_function_name = "aws-cost-optimizer"
sns_topic_arn        = "arn:aws:sns:us-east-1:xxxxxxxxxxxx:aws-cost-optimizer-alerts"
```

### 3. Confirm your email subscription

AWS sends a confirmation email immediately. **You must click "Confirm subscription"** before reports will be delivered. Check your spam folder if it does not arrive within 2 minutes.

### 4. Update the dashboard

Open `index.html` and set your API endpoint at the top of the `<script>` block:

```javascript
const HARDCODED_API_URL = 'https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/scan';
```

### 5. Test the endpoint

```bash
# Windows Git Bash
curl -X POST https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/scan --ssl-no-revoke

# macOS / Linux
curl -X POST https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/scan
```

**Expected response:**

```json
{
  "scanned_at": "2026-03-15 08:00 UTC",
  "regions_scanned": ["us-east-1", "us-west-2", "eu-west-1", "..."],
  "total_monthly_waste_usd": 3.80,
  "idle_ec2": [
    {
      "InstanceId": "i-016e2f0ad3a8ca323",
      "Name": "remote-workforce-dashboard",
      "InstanceType": "t3.micro",
      "AvgCPU7d": 0.56,
      "MonthlyCost": 3.80,
      "Action": "Stop instance"
    }
  ],
  "unattached_ebs": [],
  "s3_no_lifecycle": [],
  "ri_opportunities": []
}
```

---

## Host the Dashboard on S3

```bash
BUCKET="aws-cost-optimizer-dashboard-YOUR_USERNAME"
REGION="us-east-1"

aws s3 mb s3://$BUCKET --region $REGION

aws s3 website s3://$BUCKET \
  --index-document index.html \
  --error-document index.html

aws s3api put-public-access-block \
  --bucket $BUCKET \
  --public-access-block-configuration \
  "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false"

aws s3api put-bucket-policy \
  --bucket $BUCKET \
  --policy file://bucket-policy.json

aws s3 cp index.html s3://$BUCKET/index.html \
  --content-type "text/html"

echo "Live at: http://$BUCKET.s3-website-$REGION.amazonaws.com"
```

---

## Enable Slack (Optional)

Get your webhook URL from: **Slack → Your App → Incoming Webhooks → Add New Webhook**

```hcl
# terraform.tfvars
slack_webhook_url = "https://hooks.slack.com/services/T.../B.../..."
```

```bash
terraform apply   # Updates the Lambda environment variable — no downtime
```

---

## Monthly Cost: $0.00

| Service | Monthly usage | Free tier limit | Cost |
|---|---|---|---|
| AWS Lambda | ~5 invocations · ~5 min each | 1M requests + 400K GB-sec | **$0.00** |
| EventBridge | 1 scheduled rule | Free for scheduled rules | **$0.00** |
| SNS | ~5 email deliveries | 1,000 email notifications | **$0.00** |
| CloudWatch Metrics | ~200 API calls per scan | 1M API calls/month | **$0.00** |
| API Gateway (HTTP) | ~10 manual calls/month | 1M calls/month (first 12 months) | **$0.00** |
| S3 | 1 HTML file + read requests | 5GB + 20K GETs/month | **$0.00** |

### Cost traps deliberately avoided

| Trap | Cost if hit | How this project avoids it |
|---|---|---|
| NAT Gateway | $32+/month | Lambda runs outside any VPC — calls public AWS APIs directly over HTTPS |
| CloudWatch Logs (no expiry) | $0.50/GB/month | `retention_in_days = 14` set explicitly in Terraform |
| REST API vs HTTP API | 3.5× more expensive per request | HTTP API used — simpler configuration, built-in CORS support |
| S3 Terraform state + DynamoDB lock | ~$0.05/month | Local state is sufficient for a single-developer portfolio project |

---

## Project Structure

```
aws-cost-optimizer/
├── lambda/
│   └── lambda_function.py        # EC2/EBS/S3/RI scanners, right-sizing, Slack, report
├── terraform/
│   ├── main.tf                   # All 11 AWS resources
│   ├── variables.tf              # Input variables with validation rules
│   ├── outputs.tf                # API endpoint, Lambda name, SNS ARN
│   ├── terraform.tfvars          # Your config — GITIGNORED, never commit this file
│   └── terraform.tfvars.example  # Safe template — commit this one
├── index.html                    # Live dashboard — no framework, no build step
├── bucket-policy.json            # S3 public read policy for static website hosting
├── .gitignore                    # Excludes tfstate, tfvars, .terraform/
└── README.md
```

---

## Cleanup

```bash
cd terraform
terraform destroy
# Type "yes"
# Removes all 11 resources in ~30 seconds
```

Run this when the portfolio demo is complete. All resources are on the free tier, but cleanup is good hygiene — it leaves no orphaned infrastructure in your account.

---

## FAQ

**Can I run this on a client's AWS account?**  
Yes. The IAM policy is entirely read-only. Share the policy JSON from `terraform/main.tf` with their security team before requesting credentials.

**Will it scan all AWS regions automatically?**  
Yes. On startup the Lambda calls `describe_regions()` and scans every enabled region concurrently. Opt-in regions are included only if enabled on the account.

**What happens if a region scan fails?**  
Each region scan is individually wrapped in a `try/except` block. Failures are logged and skipped without affecting the rest of the scan. The report lists which regions were successfully scanned.

**Can I change the CPU idle threshold?**  
Yes. The 5% threshold is a constant in `lambda_function.py`. Change it and redeploy with `terraform apply`.

**Does this persist any scan data?**  
No. The Lambda generates the report in memory, publishes to SNS, and returns the JSON payload. Nothing is written to a database. The browser caches the last result in `localStorage` on your machine only — it is never transmitted anywhere.

**Why API Gateway and not a Lambda Function URL?**  
API Gateway HTTP API is the established integration pattern with well-documented CORS configuration. Lambda Function URLs require an additional resource-based policy grant that is easy to misconfigure. With concurrent scanning finishing in ~6 seconds, API Gateway's 29-second timeout is not a constraint.

---

## License

[MIT](LICENSE) — free to use, modify, and distribute with attribution.

---

<div align="center">
<sub>Built to demonstrate serverless architecture, Infrastructure as Code, and cloud cost engineering. Not affiliated with Amazon Web Services.</sub>
</div>