# AWS Cost Optimization Dashboard

![Python](https://img.shields.io/badge/Python-3.12-blue?style=flat-square&logo=python&logoColor=white)
![Terraform](https://img.shields.io/badge/Terraform-≥1.5-purple?style=flat-square&logo=terraform&logoColor=white)
![AWS Lambda](https://img.shields.io/badge/AWS-Lambda-orange?style=flat-square&logo=amazonaws&logoColor=white)
![Cost](https://img.shields.io/badge/Monthly%20Cost-$0.00-brightgreen?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)

**🔴 Live Demo → [http://aws-cost-opt123.s3-website-us-east-1.amazonaws.com](http://aws-cost-opt123.s3-website-us-east-1.amazonaws.com)**

---

I built this because I kept seeing the same thing on AWS accounts servers sitting idle, storage volumes nobody deleted, S3 buckets growing forever with no lifecycle rules. AWS doesn't tell you about any of it. It just bills you.

So I built a scanner that runs every Monday morning, checks every region in the account, and emails me exactly what's being wasted and how much it costs. First time I ran it on my own account it found a t3.micro that had been running for a week at 0.56% CPU — $45.60/year, doing nothing.

---

## What it finds

- **Idle EC2 instances** — anything running below 5% average CPU for 7 days. It also tells you what to do: stop it entirely, or downsize to a smaller instance type if there's some workload
- **Unattached EBS volumes** — storage that's just sitting there in "available" state, not attached to anything, still costing money every month
- **S3 buckets without lifecycle rules** — buckets that will just keep accumulating to Standard storage forever because nobody set an expiry or transition policy
- **Reserved Instance opportunities** — instances that have been running on-demand for 30+ days that would be 30–40% cheaper on a 1-year RI

---

## How it works

Lambda runs on a cron every Monday at 08:00 UTC. It discovers all enabled regions, scans them all at the same time using Python threads, and generates a report. The report goes out via SNS email and optionally Slack. There's also a web dashboard that lets you trigger a scan manually and see the results live.

Everything is Terraform — one `terraform apply` and it's all up. One `terraform destroy` and it's all gone.

```
EventBridge (Monday 08:00 UTC)
    → Lambda
        → ThreadPoolExecutor across all enabled regions
            → EC2 Scanner + CloudWatch CPU metrics
            → EBS Volume Scanner
            → RI Coverage Analyser
        → S3 Scanner (global, runs once)
        → Cost Report
    → SNS email + Slack
    → API Gateway → live dashboard
```

---

## Stack

| Thing | What I used |
|---|---|
| Language | Python 3.12 |
| AWS SDK | boto3 |
| Infrastructure | Terraform ≥ 1.5 |
| Compute | AWS Lambda |
| Scheduler | Amazon EventBridge |
| Alerts | SNS + Slack webhook |
| API | API Gateway v2 (HTTP) |
| Frontend | Plain HTML/CSS/JS — no framework |
| Dashboard hosting | S3 static website |

---

## Getting it running

### What you need first

```bash
# AWS CLI and Terraform installed
aws --version
terraform -version

# Credentials configured
aws configure
```

> Use a proper IAM user, not your root account. Create one with just the permissions Terraform needs to deploy this, then deactivate the key when you're done.

### Deploy

```bash
git clone https://github.com/YOUR_USERNAME/aws-cost-optimizer
cd aws-cost-optimizer/terraform

cp terraform.tfvars.example terraform.tfvars
# Fill in your region, email, and optionally a Slack webhook
```

```bash
terraform init
terraform plan   # always read this before applying
terraform apply
```

Takes about 45 seconds. When it's done you'll see something like:

```
api_endpoint = "https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/scan"
```

### Confirm your email

AWS sends a confirmation email straight away. You need to click **"Confirm subscription"** or the reports won't actually arrive. Check spam if you don't see it.

### Connect the dashboard

Open `index.html`, find this line near the top of the script block, and paste in your endpoint:

```javascript
const HARDCODED_API_URL = 'https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/scan';
```

### Test it works

```bash
# Windows Git Bash
curl -X POST https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/scan --ssl-no-revoke

# Mac / Linux
curl -X POST https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/scan
```

If you get back a JSON payload with `scanned_at`, `regions_scanned`, and `idle_ec2` — you're good.

---

## Hosting the dashboard on S3

```bash
BUCKET="aws-cost-optimizer-dashboard-YOUR_USERNAME"

aws s3 mb s3://$BUCKET --region us-east-1
aws s3 website s3://$BUCKET --index-document index.html
aws s3api put-public-access-block \
  --bucket $BUCKET \
  --public-access-block-configuration \
  "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false"
aws s3api put-bucket-policy --bucket $BUCKET --policy file://bucket-policy.json
aws s3 cp index.html s3://$BUCKET/index.html --content-type "text/html"
```

---

## Adding Slack

Get a webhook URL from your Slack app's Incoming Webhooks section and drop it in `terraform.tfvars`:

```hcl
slack_webhook_url = "https://hooks.slack.com/services/..."
```

Then `terraform apply` again. That's it.

---

## Cost

Runs entirely on the free tier. I pay $0.00/month to keep this live.

| Service | Free tier | What I use |
|---|---|---|
| Lambda | 1M requests + 400K GB-sec/month | ~5 invocations |
| EventBridge | Free for scheduled rules | 1 rule |
| SNS | 1,000 email notifications/month | ~5 emails |
| CloudWatch | 1M API calls/month | ~200 per scan |
| API Gateway | 1M calls/month (first 12 months) | ~10 manual calls |
| S3 | 5GB + 20K GETs/month | 1 HTML file |

**Things I deliberately avoided to keep the cost at zero:**

- No VPC → no NAT Gateway ($32+/month)
- CloudWatch Logs retention set to 14 days (not infinite)
- HTTP API not REST API (3.5× cheaper per million requests)
- Local Terraform state (no S3 backend + DynamoDB)

---

## Security

The Lambda role is **read-only**. Here's every permission it has:

| Permission | Why |
|---|---|
| `ec2:Describe*` | Read running instances, volumes, regions, reserved instances |
| `s3:ListAllMyBuckets`, `s3:GetBucketLifecycleConfiguration` | Check bucket lifecycle configs |
| `cloudwatch:GetMetricStatistics` | Pull CPU utilisation data |
| `sns:Publish` | Send the report — scoped to the specific topic ARN, not `*` |
| `logs:CreateLogStream`, `logs:PutLogEvents` | Lambda execution logs |

It cannot stop, start, delete, create, or modify anything. If you want to share it with a client before deploying on their account, just show them the IAM policy in `terraform/main.tf` — everything is explicit.

**Files you should never commit:**

```
terraform/terraform.tfvars        ← has your email in it
terraform/*.tfstate               ← has real resource IDs and ARNs
terraform/*.tfstate.backup
terraform/.terraform/
```

The `.gitignore` already covers all of these. Run `git status` before pushing just to be sure.

---

## Project structure

```
aws-cost-optimizer/
├── lambda/
│   └── lambda_function.py        # all the scanning logic lives here
├── terraform/
│   ├── main.tf                   # every AWS resource
│   ├── variables.tf
│   ├── outputs.tf
│   ├── terraform.tfvars          # your config — gitignored
│   └── terraform.tfvars.example  # safe to commit
├── index.html                    # the dashboard
├── bucket-policy.json
├── .gitignore
└── README.md
```

---

## Tearing it down

```bash
cd terraform && terraform destroy
```

Removes everything in about 30 seconds. Good habit to run this after a demo if you're not using it actively.

---

## Things I'd add next

- **Multi-account support** — the real value for consulting is running this across an entire AWS Organization, not just one account. `sts:AssumeRole` into each member account and aggregate the findings
- **RDS idle detection** — same CPU logic but for databases, which are typically 2–4× more expensive than EC2
- **Historical tracking** — store each weekly scan in DynamoDB so you can see whether waste is trending up or down over time
- **Slack interactive buttons** — click "Stop instance" directly from the Slack alert instead of having to go to the console

---

## License

MIT
