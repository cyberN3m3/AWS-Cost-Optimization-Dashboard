terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      ManagedBy   = "Terraform"
      Environment = "portfolio"
      Version     = "3.0"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id    = data.aws_caller_identity.current.account_id
  log_group_arn = "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/lambda/${var.project_name}"
}

# ------------------------------------------------------------------ alerts --

resource "aws_sns_topic" "cost_alerts" {
  name              = "${var.project_name}-alerts"
  kms_master_key_id = "alias/aws/sns"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.cost_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ------------------------------------------------------- private reports --

resource "aws_s3_bucket" "reports" {
  #checkov:skip=CKV_AWS_18:Access logging would need a second bucket; only the Lambda writes here
  #checkov:skip=CKV_AWS_144:Cross-region replication is not worth the cost for weekly reports
  #checkov:skip=CKV_AWS_145:SSE-S3 encryption is used; a customer managed KMS key costs $1/month
  #checkov:skip=CKV2_AWS_62:No event notifications are needed on report uploads
  bucket_prefix = "${var.project_name}-reports-"
  force_destroy = true # reports are regenerated weekly; destroy removes them with the stack
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket                  = aws_s3_bucket.reports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "reports" {
  bucket = aws_s3_bucket.reports.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  rule {
    id     = "expire-old-reports"
    status = "Enabled"
    filter {
      prefix = "reports/"
    }
    expiration {
      days = var.report_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  rule {
    id     = "abort-failed-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_policy" "reports_tls_only" {
  bucket     = aws_s3_bucket.reports.id
  depends_on = [aws_s3_bucket_public_access_block.reports]
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.reports.arn, "${aws_s3_bucket.reports.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# ------------------------------------------------------------- the scanner --

resource "aws_iam_role" "lambda_exec" {
  name = "${var.project_name}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "lambda_permissions" {
  name = "${var.project_name}-policy"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadOnlyInventory"
        Effect = "Allow"
        Action = [
          "ec2:DescribeRegions",
          "ec2:DescribeInstances",
          "ec2:DescribeVolumes",
          "ec2:DescribeSnapshots",
          "ec2:DescribeImages",
          "ec2:DescribeAddresses",
          "ec2:DescribeNatGateways",
          "elasticloadbalancing:DescribeLoadBalancers",
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeTargetHealth",
          "cloudwatch:GetMetricStatistics",
          "s3:ListAllMyBuckets",
        ]
        Resource = "*" # these calls cannot be scoped to specific resources
      },
      {
        Sid      = "ReadLogGroupSettings"
        Effect   = "Allow"
        Action   = ["logs:DescribeLogGroups"]
        Resource = "arn:aws:logs:*:${local.account_id}:log-group:*"
      },
      {
        Sid      = "ReadBucketLifecycleRules"
        Effect   = "Allow"
        Action   = ["s3:GetLifecycleConfiguration"]
        Resource = "arn:aws:s3:::*"
      },
      {
        Sid    = "ReadOnlyBillingAndPrices"
        Effect = "Allow"
        Action = [
          "ce:GetCostAndUsage",
          "ce:GetCostForecast",
          "ce:GetSavingsPlansPurchaseRecommendation",
          "pricing:GetProducts",
        ]
        Resource = "*"
      },
      {
        Sid      = "WriteReportsOnly"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.reports.arn}/reports/*"
      },
      {
        Sid      = "PublishToOurTopicOnly"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.cost_alerts.arn
      },
      {
        Sid      = "WriteOwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${local.log_group_arn}:*"
      },
      {
        Sid      = "XRayTracing"
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = "*"
      },
    ]
  })
}

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda"
  output_path = "${path.module}/lambda_function.zip"
  excludes    = ["__pycache__"]
}

resource "aws_cloudwatch_log_group" "lambda_logs" {
  #checkov:skip=CKV_AWS_338:14 days is enough for a weekly job and keeps log storage near zero
  #checkov:skip=CKV_AWS_158:Logs hold no secrets; a customer managed KMS key costs $1/month
  name              = "/aws/lambda/${var.project_name}"
  retention_in_days = 14
}

resource "aws_lambda_function" "cost_optimizer" {
  #checkov:skip=CKV_AWS_117:Only calls AWS APIs; putting it in a VPC would need a NAT gateway (~$32/month)
  #checkov:skip=CKV_AWS_115:Reserving concurrency fails on new accounts that have a limit of 10
  #checkov:skip=CKV_AWS_116:A failed weekly run is visible in the logs and simply runs again next week
  #checkov:skip=CKV_AWS_272:Code signing is out of scope for a single-author portfolio tool
  #checkov:skip=CKV_AWS_173:Environment variables hold only resource names, no secrets
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  function_name = var.project_name
  role          = aws_iam_role.lambda_exec.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.12"
  timeout       = 600
  memory_size   = 256

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      SNS_TOPIC_ARN     = aws_sns_topic.cost_alerts.arn
      REPORT_BUCKET     = aws_s3_bucket.reports.id
      SLACK_WEBHOOK_URL = var.slack_webhook_url
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda_logs]
}

resource "aws_cloudwatch_event_rule" "weekly" {
  name                = "${var.project_name}-weekly-trigger"
  description         = "Run the cost scan every Monday at 08:00 UTC"
  schedule_expression = "cron(0 8 ? * MON *)"
}

resource "aws_cloudwatch_event_target" "invoke_lambda" {
  rule      = aws_cloudwatch_event_rule.weekly.name
  target_id = "CostOptimizerLambda"
  arn       = aws_lambda_function.cost_optimizer.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cost_optimizer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.weekly.arn
}

# There is deliberately no API Gateway. Earlier versions exposed POST /scan
# with no login, which let anyone run a scan and read the results. Scans now
# run on the schedule or with `aws lambda invoke`, which needs IAM access.
