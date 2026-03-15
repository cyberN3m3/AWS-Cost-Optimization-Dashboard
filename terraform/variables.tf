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
  description = "Slack incoming webhook URL (optional — leave empty to skip Slack alerts)"
  type        = string
  default     = ""
}
