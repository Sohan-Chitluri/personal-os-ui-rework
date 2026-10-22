# Personal OS — Terraform root module (M1-D deployment preparation).
# No resources are provisioned by this repository until an operator runs
# `terraform apply` with explicit environment vars. See docs/DEPLOYMENT.md.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state (S3 + DynamoDB locking). Backend config is intentionally NOT
  # committed; pass it at init time so the same module serves dev and prod:
  #   terraform init -backend-config=envs/<env>.backend.hcl
  backend "s3" {
    # Placeholder values are overridden by -backend-config flags.
    # Key/bucket/region must match the state bucket created once by the
    # bootstrap step in docs/DEPLOYMENT.md (bootstrap-remote-state).
  }
}

# Primary provider: regional resources (EC2, RDS, SQS, IAM, CloudWatch).
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "personal-os"
      ManagedBy = "terraform"
      Module    = "deploy/terraform"
      Owner     = var.owner_tag
    }
  }
}

# CloudFront certificates must live in us-east-1.
provider "aws" {
  alias  = "useast1"
  region = "us-east-1"
}
