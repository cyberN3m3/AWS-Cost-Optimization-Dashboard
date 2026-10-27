output "lambda_function_name" {
  description = "Lambda function name"
  value       = aws_lambda_function.cost_optimizer.function_name
}

output "report_bucket" {
  description = "Private bucket that holds every report"
  value       = aws_s3_bucket.reports.id
}

output "sns_topic_arn" {
  description = "SNS topic that sends the weekly summary"
  value       = aws_sns_topic.cost_alerts.arn
}

output "dashboard_url" {
  description = "HTTPS address of the dashboard"
  value       = var.deploy_dashboard ? "https://${aws_cloudfront_distribution.dashboard[0].domain_name}" : null
}

output "run_scan_now" {
  description = "Run a scan by hand (needs your AWS credentials)"
  value       = "aws lambda invoke --function-name ${aws_lambda_function.cost_optimizer.function_name} --region ${var.aws_region} --cli-read-timeout 900 output.json && cat output.json"
}

output "download_latest_report" {
  description = "Copy the newest report to your machine, then open it in a browser or load the JSON into the dashboard"
  value       = "aws s3 cp s3://${aws_s3_bucket.reports.id}/reports/latest.json ."
}
