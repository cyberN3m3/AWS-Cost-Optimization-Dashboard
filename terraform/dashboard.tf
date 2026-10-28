# The dashboard is a static page served over HTTPS by CloudFront from a
# private bucket. It never calls AWS: it shows sample data, or a report
# file you open from your own computer.

resource "aws_s3_bucket" "dashboard" {
  #checkov:skip=CKV_AWS_18:Access logging would need a second bucket; CloudFront is the only reader
  #checkov:skip=CKV_AWS_144:Cross-region replication is not needed for one static file
  #checkov:skip=CKV_AWS_145:SSE-S3 is enough for a public web page
  #checkov:skip=CKV2_AWS_62:No event notifications are needed
  #checkov:skip=CKV_AWS_21:The page is rebuilt from Git, so versioning adds nothing
  count         = var.deploy_dashboard ? 1 : 0
  bucket_prefix = "${var.project_name}-dashboard-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "dashboard" {
  count                   = var.deploy_dashboard ? 1 : 0
  bucket                  = aws_s3_bucket.dashboard[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dashboard" {
  count  = var.deploy_dashboard ? 1 : 0
  bucket = aws_s3_bucket.dashboard[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# One file, so nothing to expire, but without any rule CostPatrol reports its
# own bucket. Cleaning up failed uploads is the rule that actually applies.
resource "aws_s3_bucket_lifecycle_configuration" "dashboard" {
  count  = var.deploy_dashboard ? 1 : 0
  bucket = aws_s3_bucket.dashboard[0].id

  rule {
    id     = "abort-failed-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_object" "dashboard_page" {
  count         = var.deploy_dashboard ? 1 : 0
  bucket        = aws_s3_bucket.dashboard[0].id
  key           = "index.html"
  source        = "${path.module}/../frontend/index.html"
  etag          = filemd5("${path.module}/../frontend/index.html")
  content_type  = "text/html"
  cache_control = "max-age=300"
}

resource "aws_cloudfront_origin_access_control" "dashboard" {
  count                             = var.deploy_dashboard ? 1 : 0
  name                              = "${var.project_name}-dashboard"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_response_headers_policy" "dashboard" {
  count = var.deploy_dashboard ? 1 : 0
  name  = "${var.project_name}-security-headers"

  security_headers_config {
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      preload                    = true
      override                   = true
    }
    content_security_policy {
      content_security_policy = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'none'; frame-ancestors 'none'"
      override                = true
    }
  }
}

resource "aws_cloudfront_distribution" "dashboard" {
  #checkov:skip=CKV_AWS_68:AWS WAF costs at least $5/month; the page is static and calls no API
  #checkov:skip=CKV2_AWS_47:Same as above, no WAF
  #checkov:skip=CKV_AWS_86:Access logs are not needed for a portfolio page
  #checkov:skip=CKV_AWS_310:Origin failover is not needed for one static file
  #checkov:skip=CKV_AWS_374:The page is meant to be public worldwide
  #checkov:skip=CKV_AWS_174:The default CloudFront certificate does not allow a minimum TLS setting
  #checkov:skip=CKV2_AWS_42:A custom domain certificate needs a domain; the default one is used
  count               = var.deploy_dashboard ? 1 : 0
  enabled             = true
  comment             = "${var.project_name} dashboard"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  http_version        = "http2and3"

  origin {
    domain_name              = aws_s3_bucket.dashboard[0].bucket_regional_domain_name
    origin_id                = "dashboard-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.dashboard[0].id
  }

  default_cache_behavior {
    target_origin_id           = "dashboard-s3"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = "658327ea-f89d-4fab-a63d-7e88639e58f6" # Managed-CachingOptimized
    response_headers_policy_id = aws_cloudfront_response_headers_policy.dashboard[0].id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

resource "aws_s3_bucket_policy" "dashboard" {
  count      = var.deploy_dashboard ? 1 : 0
  bucket     = aws_s3_bucket.dashboard[0].id
  depends_on = [aws_s3_bucket_public_access_block.dashboard]
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "CloudFrontReadsThePage"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.dashboard[0].arn}/*"
        Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.dashboard[0].arn } }
      },
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.dashboard[0].arn, "${aws_s3_bucket.dashboard[0].arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  })
}
