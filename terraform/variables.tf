variable "aws_region" {
  description = "AWS region where all resources will be created"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Name prefix used for every AWS resource"
  type        = string
  default     = "aws-cost-optimizer"
}

variable "alert_email" {
  description = "Email address that will receive the weekly cost report"
  type        = string

  validation {
    condition     = can(regex("^[\\w.+-]+@[\\w-]+\\.[a-zA-Z]{2,}$", var.alert_email))
    error_message = "Please provide a valid email address."
  }
}

variable "slack_webhook_url" {
  description = "Slack incoming webhook URL (optional; leave empty to skip Slack)"
  type        = string
  default     = ""
}

variable "report_retention_days" {
  description = "How many days to keep each weekly report in the private S3 bucket"
  type        = number
  default     = 180
}

variable "deploy_dashboard" {
  description = "Host the dashboard page on CloudFront (HTTPS) from a private S3 bucket"
  type        = bool
  default     = true
}
