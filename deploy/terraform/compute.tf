# ---------------------------------------------------------------------------
# ALB + target groups + ASG launch template.
# API and worker are SEPARATE process definitions:
#   - API      : uvicorn (personal_os.api.main:app), health via /health + /ready
#   - Worker   : personal_os.services.worker:CaptureWorker loop via a thin
#                module runner, no listening port, health via systemd
#                watchdog + CloudWatch heartbeat metric
# ---------------------------------------------------------------------------

resource "random_password" "alb_origin_verify" {
  length  = 32
  special = false
}

resource "aws_lb" "main" {
  name               = "${var.name_prefix}-${var.environment}"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  enable_deletion_protection = false

  tags = { Name = "${var.name_prefix}-${var.environment}-alb" }
}

# ALB listener: HTTP (port 80) when using default CloudFront domain,
# HTTPS (port 443 with ACM) when custom domain is enabled.
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = local.custom_domain_enabled ? 443 : 80
  protocol          = local.custom_domain_enabled ? "HTTPS" : "HTTP"
  ssl_policy        = local.custom_domain_enabled ? "ELBSecurityPolicy-TLS13-1-2-2021-06" : null
  certificate_arn   = local.custom_domain_enabled ? aws_acm_certificate_validation.api[0].certificate_arn : null

  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Not found"
      status_code  = "404"
    }
  }
}

resource "aws_lb_target_group" "api" {
  name        = "${var.name_prefix}-${var.environment}-api"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"

  health_check {
    enabled             = true
    path                = "/api/v1/health"
    port                = "traffic-port"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  deregistration_delay = 30

  tags = { Name = "${var.name_prefix}-${var.environment}-api-tg" }
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    path_pattern {
      values = ["/api/*"]
    }
  }

  dynamic "condition" {
    for_each = var.enable_cloudfront ? [1] : []
    content {
      http_header {
        http_header_name = "X-Origin-Verify"
        values           = [random_password.alb_origin_verify.result]
      }
    }
  }
}

# The listener's default action rejects every path; /api/* is forwarded only
# when the CloudFront-injected origin header matches.
# ---------------------------------------------------------------------------
# IAM: instance profile with least privilege for runtime needs.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app" {
  name               = "${var.name_prefix}-${var.environment}-app-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json

  tags = { Name = "${var.name_prefix}-${var.environment}-app-role" }
}

data "aws_iam_policy_document" "app" {
  statement {
    sid    = "SecretsRead"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = [
      "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.name_prefix}/${var.environment}/database*",
      "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.name_prefix}/${var.environment}/companion-jwt*",
      "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.name_prefix}/${var.environment}/openrouter-api-key*",
    ]
  }

  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/personal-os/${var.environment}/*"]
  }

  statement {
    sid    = "CloudWatchPutMetrics"
    effect = "Allow"
    actions = [
      "cloudwatch:PutMetricData",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "CaptureQueueAccess"
    effect = "Allow"
    actions = [
      "sqs:SendMessage",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
    ]
    resources = ["arn:aws:sqs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${var.name_prefix}-${var.environment}-capture"]
  }

  # SSM Session Manager access (no SSH).
  statement {
    sid    = "ArtifactWheelRead"
    effect = "Allow"
    actions = [
      "s3:GetObject",
    ]
    resources = ["arn:aws:s3:::${var.name_prefix}-${var.environment}-artifacts/*"]
  }

  statement {
    sid    = "SSMSessionManager"
    effect = "Allow"
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"]
  }
}

data "aws_caller_identity" "current" {}

resource "aws_iam_role_policy" "app" {
  name   = "${var.name_prefix}-${var.environment}-app-policy"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app.json
}

resource "aws_iam_role_policy_attachment" "app_ssm_core" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "app_cw_agent" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

resource "aws_iam_instance_profile" "app" {
  name = "${var.name_prefix}-${var.environment}-app-profile"
  role = aws_iam_role.app.name
}

# ---------------------------------------------------------------------------
# Launch template + ASG (one instance of each role for now; SQS scaling of the
# worker is added when the queue adapter lands).
# ---------------------------------------------------------------------------

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  user_data = base64encode(templatefile("${path.module}/templates/user_data.sh.tftpl", {
    environment           = var.environment
    app_env_setting       = var.app_env_setting
    aws_region            = var.aws_region
    db_secret_arn         = aws_secretsmanager_secret.db.arn
    companion_secret_arn  = aws_secretsmanager_secret.companion_jwt.arn
    rds_endpoint          = aws_db_instance.main.address
    sqs_queue_url         = aws_sqs_queue.capture.url
    cognito_user_pool_id  = aws_cognito_user_pool.main.id
    cognito_app_client_id = aws_cognito_user_pool_client.frontend.id
    openrouter_secret_arn = aws_secretsmanager_secret.openrouter_api_key.arn
  }))
}

resource "aws_launch_template" "app" {
  name_prefix   = "${var.name_prefix}-${var.environment}-lt-"
  description   = "Personal OS API / worker launch template"
  image_id      = var.ami_id != "" ? var.ami_id : data.aws_ami.al2023.id
  instance_type = var.api_instance_type

  iam_instance_profile {
    arn = aws_iam_instance_profile.app.arn
  }

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      volume_size           = 20
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "enabled"
  }

  network_interfaces {
    security_groups       = [aws_security_group.app.id]
    delete_on_termination = true
  }

  user_data = local.user_data

  depends_on = [
    aws_secretsmanager_secret_version.db,
    aws_secretsmanager_secret_version.companion_jwt,
  ]

  tag_specifications {
    resource_type = "instance"

    tags = {
      Name        = "${var.name_prefix}-${var.environment}-app"
      Environment = var.environment
      Role        = "api"
    }
  }

  lifecycle {
    create_before_destroy = true
    ignore_changes        = [user_data]
  }
}

# Worker launch template: same bootstrap, worker-sized instance. Separate
# template because ASG launch_template blocks don't support per-ASG overrides.
resource "aws_launch_template" "worker" {
  name_prefix   = "${var.name_prefix}-${var.environment}-lt-worker-"
  description   = "Personal OS worker launch template"
  image_id      = var.ami_id != "" ? var.ami_id : data.aws_ami.al2023.id
  instance_type = var.worker_instance_type

  iam_instance_profile {
    arn = aws_iam_instance_profile.app.arn
  }

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      volume_size           = 20
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "enabled"
  }

  network_interfaces {
    security_groups       = [aws_security_group.app.id]
    delete_on_termination = true
  }

  user_data = local.user_data

  depends_on = [
    aws_secretsmanager_secret_version.db,
    aws_secretsmanager_secret_version.companion_jwt,
  ]

  tag_specifications {
    resource_type = "instance"

    tags = {
      Name        = "${var.name_prefix}-${var.environment}-worker"
      Environment = var.environment
      Role        = "worker"
    }
  }

  lifecycle {
    create_before_destroy = true
    ignore_changes        = [user_data]
  }
}

resource "aws_autoscaling_group" "api" {
  name                = "${var.name_prefix}-${var.environment}-api-asg"
  vpc_zone_identifier = aws_subnet.private[*].id
  min_size            = 1
  max_size            = 2
  desired_capacity    = 1

  health_check_type         = "ELB"
  health_check_grace_period = 300

  target_group_arns = [aws_lb_target_group.api.arn]

  launch_template {
    id      = aws_launch_template.app.id
    version = "$Latest"
  }

  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 100
      instance_warmup        = 60
    }
  }

  tag {
    key                 = "Name"
    value               = "${var.name_prefix}-${var.environment}-api"
    propagate_at_launch = true
  }

  lifecycle {
    create_before_destroy = true
  }
}


# Worker ASG: separate process definition, same launch template. The ASG-level
# tag Role=worker overrides the launch template default so user_data starts
# pos-worker.service instead of pos-api.service.
resource "aws_autoscaling_group" "worker" {
  name                = "${var.name_prefix}-${var.environment}-worker-asg"
  vpc_zone_identifier = aws_subnet.private[*].id
  min_size            = 1
  max_size            = 1
  desired_capacity    = 1

  health_check_type         = "EC2"
  health_check_grace_period = 120

  launch_template {
    id      = aws_launch_template.worker.id
    version = "$Latest"
  }

  tag {
    key                 = "Role"
    value               = "worker"
    propagate_at_launch = true
  }

  tag {
    key                 = "Name"
    value               = "${var.name_prefix}-${var.environment}-worker"
    propagate_at_launch = true
  }
}

output "asg_worker_name" {
  value = aws_autoscaling_group.worker.name
}

output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "asg_api_name" {
  value = aws_autoscaling_group.api.name
}
