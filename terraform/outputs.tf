output "lambda_function_name" {
  description = "Lambda function name"
  value       = aws_lambda_function.cost_optimizer.function_name
}

output "sns_topic_arn" {
  description = "SNS topic receiving cost reports"
  value       = aws_sns_topic.cost_alerts.arn
}

output "api_endpoint" {
  description = "Paste this URL into the dashboard — your live scan endpoint"
  value       = "${trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")}/scan"
}

output "manual_invoke_command" {
  description = "Test the API directly with curl"
  value       = "curl -X POST ${trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")}/scan --ssl-no-revoke"
}

output "manual_lambda_command" {
  description = "Invoke Lambda directly via CLI"
  value       = "aws lambda invoke --function-name ${aws_lambda_function.cost_optimizer.function_name} --region ${var.aws_region} output.json && cat output.json"
}
