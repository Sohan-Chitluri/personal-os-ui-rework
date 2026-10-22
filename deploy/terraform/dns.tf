# ---------------------------------------------------------------------------
# ACM certificates (CloudFront cert must be in us-east-1), Route 53 records.
# DNS is validated automatically against the existing zone when custom domain is enabled.
# When custom domain is disabled, no ACM or Route 53 resources are created.
# ---------------------------------------------------------------------------

# API certificate: issued in the region (used by the ALB).
resource "aws_acm_certificate" "api" {
  count             = local.custom_domain_enabled ? 1 : 0
  domain_name       = "${var.api_subdomain}.${var.root_domain}"
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "api_validation" {
  for_each = local.custom_domain_enabled ? {
    for dvo in aws_acm_certificate.api[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  } : {}

  zone_id = var.route53_zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 60
  records = [each.value.record]
}

resource "aws_acm_certificate_validation" "api" {
  count                   = local.custom_domain_enabled ? 1 : 0
  provider                = aws
  certificate_arn         = aws_acm_certificate.api[0].arn
  validation_record_fqdns = [for r in aws_route53_record.api_validation : r.fqdn]
}

resource "aws_route53_record" "api_origin" {
  count   = local.custom_domain_enabled ? 1 : 0
  zone_id = var.route53_zone_id
  name    = "${var.api_subdomain}.${var.root_domain}"
  type    = "A"

  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = true
  }
}

# Frontend certificate: MUST be in us-east-1 (CloudFront requirement).
resource "aws_acm_certificate" "frontend" {
  count             = local.custom_domain_enabled ? 1 : 0
  provider          = aws.useast1
  domain_name       = "${var.frontend_subdomain}.${var.root_domain}"
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "frontend_validation" {
  provider = aws.useast1
  for_each = local.custom_domain_enabled ? {
    for dvo in aws_acm_certificate.frontend[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  } : {}

  zone_id = var.route53_zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 60
  records = [each.value.record]
}

resource "aws_acm_certificate_validation" "frontend" {
  count                   = local.custom_domain_enabled ? 1 : 0
  provider                = aws.useast1
  certificate_arn         = aws_acm_certificate.frontend[0].arn
  validation_record_fqdns = [for r in aws_route53_record.frontend_validation : r.fqdn]
}

# CloudFront alias record for the frontend host.
resource "aws_route53_record" "frontend_alias" {
  count   = local.custom_domain_enabled && var.enable_cloudfront ? 1 : 0
  zone_id = var.route53_zone_id
  name    = "${var.frontend_subdomain}.${var.root_domain}"
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.frontend[0].domain_name
    zone_id                = aws_cloudfront_distribution.frontend[0].hosted_zone_id
    evaluate_target_health = false
  }
}
