# ---------------------------------------------------------------------------
# S3 + CloudFront: frontend hosting and the single public entry point.
# - Frontend served from S3 via CloudFront (OAC, HTTPS, ACM in us-east-1).
# - /api/* is proxied to the internal ALB (no caching) so the frontend and API
#   share one origin/domain — no CORS configuration needed.
# - Capture delivery uses the Standard SQS queue defined in queue.tf.
# ---------------------------------------------------------------------------

# Managed CloudFront policies, looked up by name (no hard-coded IDs).
data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "cors_s3" {
  name = "Managed-CORS-S3Origin"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

data "aws_cloudfront_response_headers_policy" "security_headers" {
  name = "Managed-SecurityHeadersPolicy"
}

# Rewrite extensionless application routes to the SPA entry document. Requests
# for files such as JS/CSS retain their original URI and return real 403/404s.
resource "aws_cloudfront_function" "spa_routes" {
  name    = "${var.name_prefix}-${var.environment}-spa-routes"
  runtime = "cloudfront-js-1.0"
  publish = true
  code    = <<-JS
    function handler(event) {
      var request = event.request;
      var uri = request.uri;
      if (uri === "/") return request;
      if (uri.indexOf("/api/") === 0) return request;
      if (uri.substring(uri.lastIndexOf("/" ) + 1).indexOf(".") === -1) {
        request.uri = "/index.html";
      }
      return request;
    }
  JS
}

resource "aws_s3_bucket" "frontend" {
  bucket = "${var.name_prefix}-${var.environment}-frontend"

  tags = {
    Name        = "${var.name_prefix}-${var.environment}-frontend"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_s3_bucket_versioning" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  depends_on = [aws_s3_bucket_versioning.frontend]

  rule {
    id = "expire-old-versions"
    filter {}
    status = "Enabled"

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# CloudFront Origin Access Control (replaces the legacy OAI).
resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${var.name_prefix}-${var.environment}-frontend-oac"
  description                       = "OAC for ${var.name_prefix}-${var.environment}-frontend"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# The bucket is reachable ONLY through this distribution.
data "aws_iam_policy_document" "frontend_oac" {
  count = var.enable_cloudfront ? 1 : 0

  statement {
    sid       = "AllowCloudFrontOAC"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.frontend.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.frontend[0].arn]
    }
  }
}

resource "aws_s3_bucket_policy" "frontend" {
  count  = var.enable_cloudfront ? 1 : 0
  bucket = aws_s3_bucket.frontend.id
  policy = data.aws_iam_policy_document.frontend_oac[0].json

  depends_on = [aws_s3_bucket_public_access_block.frontend]
}

# Shared verification header so the ALB can reject traffic that did not come
# through CloudFront (defense in depth alongside the private ALB security
# group). The value is generated here, never hard-coded.
resource "aws_cloudfront_distribution" "frontend" {
  count               = var.enable_cloudfront ? 1 : 0
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${var.name_prefix}-${var.environment} frontend + API proxy"
  price_class         = var.cloudfront_price_class
  default_root_object = "index.html"

  aliases = local.custom_domain_enabled ? ["${var.frontend_subdomain}.${var.root_domain}"] : []

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "s3-frontend"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  origin {
    domain_name = local.custom_domain_enabled ? "${var.api_subdomain}.${var.root_domain}" : aws_lb.main.dns_name
    origin_id   = "alb-api"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = local.custom_domain_enabled ? "https-only" : "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }

    custom_header {
      name  = "X-Origin-Verify"
      value = random_password.alb_origin_verify.result
    }
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "s3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.cors_s3.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security_headers.id

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.spa_routes.arn
    }
  }

  # API: no caching, forward client headers/queries (except Host), HTTPS only.
  ordered_cache_behavior {
    path_pattern     = "/api/*"
    allowed_methods  = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "alb-api"

    cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security_headers.id

    viewer_protocol_policy = "https-only"
    compress               = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = local.custom_domain_enabled ? false : true
    acm_certificate_arn            = local.custom_domain_enabled ? aws_acm_certificate_validation.frontend[0].certificate_arn : null
    ssl_support_method             = local.custom_domain_enabled ? "sni-only" : null
    minimum_protocol_version       = local.custom_domain_enabled ? "TLSv1.2_2021" : null
  }

  tags = {
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

output "frontend_bucket" {
  value = aws_s3_bucket.frontend.bucket
}

output "cloudfront_distribution_id" {
  value = var.enable_cloudfront ? aws_cloudfront_distribution.frontend[0].id : null
}

output "cloudfront_domain" {
  value = var.enable_cloudfront ? aws_cloudfront_distribution.frontend[0].domain_name : null
}

output "frontend_url" {
  value = local.frontend_url
}

output "api_url_via_cloudfront" {
  value = var.enable_cloudfront ? "${local.frontend_url}/api/v1" : null
}
