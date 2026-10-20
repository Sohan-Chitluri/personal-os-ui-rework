# ---------------------------------------------------------------------------
# Security groups — least privilege, explicit rule boundaries.
# ---------------------------------------------------------------------------

resource "random_password" "companion_jwt" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret" "companion_jwt" {
  name                    = "${var.name_prefix}/${var.environment}/companion-jwt"
  recovery_window_in_days = var.environment == "production" ? 30 : 0
}

resource "aws_secretsmanager_secret_version" "companion_jwt" {
  secret_id     = aws_secretsmanager_secret.companion_jwt.id
  secret_string = jsonencode({ secret = random_password.companion_jwt.result })
}

resource "aws_secretsmanager_secret" "openrouter_api_key" {
  name                    = "${var.name_prefix}/${var.environment}/openrouter-api-key"
  recovery_window_in_days = var.environment == "production" ? 30 : 0
}

# API / worker instances: no inbound SSH. Access via SSM Session Manager.
resource "aws_security_group" "app" {
  name_prefix = "${var.name_prefix}-app-"
  description = "Personal OS API and worker instances"
  vpc_id      = aws_vpc.main.id

  # Inbound only from the ALB for the API port.
  ingress {
    description     = "HTTP from ALB only"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "All outbound (NAT: PyPI, AWS APIs, DB)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = { Name = "${var.name_prefix}-${var.environment}-app-sg" }
}

# RDS: only the app SG, port 5432.
resource "aws_security_group" "db" {
  name_prefix = "${var.name_prefix}-db-"
  description = "Personal OS RDS PostgreSQL"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "PostgreSQL from app instances only"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  egress {
    description     = "Return traffic to app instances"
    from_port       = 0
    to_port         = 0
    protocol        = "-1"
    security_groups = [aws_security_group.app.id]
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = { Name = "${var.name_prefix}-${var.environment}-db-sg" }
}

# Public ALB: only CloudFront origin-facing IP ranges may reach TLS. The API
# listener also requires the secret CloudFront origin header before forwarding.
data "aws_ec2_managed_prefix_list" "cloudfront_origin" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "alb" {
  name_prefix = "${var.name_prefix}-alb-"
  description = "Personal OS ALB"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = local.custom_domain_enabled ? "HTTPS from CloudFront" : (var.enable_cloudfront ? "HTTP from CloudFront" : "HTTP from admin/allowed CIDR")
    from_port       = local.custom_domain_enabled ? 443 : 80
    to_port         = local.custom_domain_enabled ? 443 : 80
    protocol        = "tcp"
    prefix_list_ids = var.enable_cloudfront ? [data.aws_ec2_managed_prefix_list.cloudfront_origin.id] : []
    cidr_blocks     = var.enable_cloudfront ? [] : (var.admin_office_cidr != "" ? [var.admin_office_cidr] : [])
  }

  ingress {
    description     = "HTTP from API Gateway VPC Link"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.vpc_link.id]
    cidr_blocks     = [for eip in aws_eip.nat : "${eip.public_ip}/32"]
  }

  # Egress unrestricted — the ALB may only *reach* what the target SG admits;
  # the app SG's ingress rule (port 8000 from this SG) is the actual boundary.
  # Kept open to avoid an SG reference cycle with the app SG (inline rules).
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = { Name = "${var.name_prefix}-${var.environment}-alb-sg" }
}
