# ---------------------------------------------------------------------------
# Variables — Personal OS deployment (M1-D)
# ---------------------------------------------------------------------------

variable "aws_region" {
  description = "AWS region for all regional resources."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment: dev | staging | production. Maps to app Settings.environment (staging|production only; use staging until API is hardened)."
  type        = string
  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "environment must be dev, staging, or production."
  }
}

variable "app_env_setting" {
  description = "Value passed as the app's ENVIRONMENT setting. 'dev' instances run with staging so auth does not trust a local dev token."
  type        = string
  default     = "staging"
}

variable "name_prefix" {
  description = "Resource name prefix."
  type        = string
  default     = "personal-os"
}

variable "owner_tag" {
  description = "Cost/ownership tag applied to all resources."
  type        = string
  default     = "personal-os-team"
}

# --- Domain / TLS -----------------------------------------------------------

variable "root_domain" {
  description = "Existing apex domain already hosted in Route 53 (e.g. pos-deku.com). Leave empty to use the default CloudFront domain without Route 53."
  type        = string
  default     = ""
}

variable "frontend_subdomain" {
  description = "Frontend host (CloudFront alias when custom domain is enabled)."
  type        = string
  default     = "app"
}

variable "api_subdomain" {
  description = "API host (ALB alias via CloudFront when custom domain is enabled)."
  type        = string
  default     = "api"
}

variable "route53_zone_id" {
  description = "Route 53 zone id of root_domain. Leave empty if not using Route 53 or custom domain."
  type        = string
  default     = ""
}

variable "enable_cloudfront" {
  description = "Enable CloudFront distribution. Set to false to bypass CloudFront account verification or use external hosting."
  type        = bool
  default     = true
}

variable "external_frontend_url" {
  description = "External frontend HTTPS URL when CloudFront is disabled (e.g. https://personal-os-staging.vercel.app)."
  type        = string
  default     = ""
}

variable "extra_callback_urls" {
  description = "Additional OAuth callback URLs permitted for the Cognito frontend client (e.g. localhost for development)."
  type        = list(string)
  default     = []
}

variable "extra_logout_urls" {
  description = "Additional OAuth logout URLs permitted for the Cognito frontend client (e.g. localhost for development)."
  type        = list(string)
  default     = []
}

locals {
  custom_domain_enabled = var.root_domain != "" && var.route53_zone_id != ""
  frontend_fqdn         = local.custom_domain_enabled ? "${var.frontend_subdomain}.${var.root_domain}" : (var.enable_cloudfront ? aws_cloudfront_distribution.frontend[0].domain_name : "")
  frontend_url          = var.enable_cloudfront ? "https://${local.frontend_fqdn}" : (var.external_frontend_url != "" ? var.external_frontend_url : "https://app.staging.local")
  api_origin_domain     = local.custom_domain_enabled ? "${var.api_subdomain}.${var.root_domain}" : aws_lb.main.dns_name
}

# --- Compute ----------------------------------------------------------------

variable "api_instance_type" {
  description = "API instance type."
  type        = string
  default     = "t3.small"
}

variable "worker_instance_type" {
  description = "Capture worker instance type."
  type        = string
  default     = "t3.small"
}

variable "ami_id" {
  description = "Amazon Linux 2023 AMI. Leave empty to resolve the latest AL2023 x86_64 at apply time."
  type        = string
  default     = ""
}

variable "ssh_allowed_cidrs" {
  description = "CIDRs allowed SSH/Ssm access to EC2. Keep empty: instance access is via SSM Session Manager, no SSH port opened."
  type        = list(string)
  default     = []
}

variable "admin_office_cidr" {
  description = "CIDR allowed to reach the ALB. Keep restrictive during bring-up; '' means public internet (CloudFront-only origin restriction still applies)."
  type        = string
  default     = ""
}

# --- RDS --------------------------------------------------------------------

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_multi_az" {
  description = "Multi-AZ for RDS (true in production only)."
  type        = bool
  default     = false
}

variable "db_allocated_storage" {
  description = "RDS storage GB (gp3)."
  type        = number
  default     = 20
}

variable "db_max_allocated_storage" {
  description = "RDS storage autoscaling ceiling."
  type        = number
  default     = 100
}

variable "db_backup_retention_days" {
  description = "Automated backup retention (0 disables; never disable in production)."
  type        = number
  default     = 7
}


variable "db_master_username" {
  description = "RDS master username (not a secret; the password is generated and stored in Secrets Manager)."
  type        = string
  default     = "personalos_admin"
}

# --- Frontend ---------------------------------------------------------------

variable "frontend_build_dir" {
  description = "Local build output directory uploaded by scripts/deploy-frontend.sh."
  type        = string
  default     = "ui/dist/client"
}

variable "cloudfront_price_class" {
  description = "CloudFront price class. PriceClass_100 covers NA/EU — cheapest; widen only for real global traffic."
  type        = string
  default     = "PriceClass_100"
}

variable "alert_email" {
  description = "Email subscribed to the alarm SNS topic. Empty disables subscription (topic still created)."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  description = "CloudWatch log retention."
  type        = number
  default     = 30
}

variable "vpc_cidr" {
  description = "VPC CIDR."
  type        = string
  default     = "10.40.0.0/16"
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "sns_subscription_pending" {
  description = "Reminder that an email subscription needs confirmation."
  value       = var.alert_email != "" ? "check ${var.alert_email} inbox for SNS confirmation" : "no email configured"
}
