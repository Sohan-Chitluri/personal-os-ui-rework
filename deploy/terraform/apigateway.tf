# ---------------------------------------------------------------------------
# API Gateway HTTP API (v2) HTTPS bridge for AWS Amplify frontend.
# Integrates privately with the existing ALB via VPC Link (V2).
# Enforces native Cognito JWT authorization at the AWS edge.
# ---------------------------------------------------------------------------

resource "aws_security_group" "vpc_link" {
  name_prefix = "${var.name_prefix}-vpclink-"
  description = "API Gateway VPC Link to ALB"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "HTTP to VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name        = "${var.name_prefix}-${var.environment}-vpclink-sg"
    Environment = var.environment
  }
}

resource "aws_apigatewayv2_vpc_link" "alb" {
  name               = "${var.name_prefix}-${var.environment}-alb-vpclink"
  security_group_ids = [aws_security_group.vpc_link.id]
  subnet_ids         = aws_subnet.private[*].id

  tags = {
    Name        = "${var.name_prefix}-${var.environment}-alb-vpclink"
    Environment = var.environment
  }
}

resource "aws_apigatewayv2_api" "http" {
  name          = "${var.name_prefix}-${var.environment}-http-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_credentials = true
    allow_headers     = ["*"]
    allow_methods     = ["*"]
    allow_origins = distinct(compact([
      var.external_frontend_url,
      "https://staging.d1gbedl9yrj8s.amplifyapp.com",
      "http://localhost:8080",
    ]))
    max_age = 300
  }

  tags = {
    Name        = "${var.name_prefix}-${var.environment}-http-api"
    Environment = var.environment
  }
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.http.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "${var.name_prefix}-${var.environment}-cognito"

  jwt_configuration {
    audience = [aws_cognito_user_pool_client.frontend.id]
    issuer   = "https://${aws_cognito_user_pool.main.endpoint}"
  }
}

resource "aws_apigatewayv2_integration" "alb" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "HTTP_PROXY"
  integration_method     = "ANY"
  connection_type        = "VPC_LINK"
  connection_id          = aws_apigatewayv2_vpc_link.alb.id
  integration_uri        = aws_lb_listener.https.arn
  payload_format_version = "1.0"
}

resource "aws_apigatewayv2_route" "api" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "ANY /{proxy+}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
  target             = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "api_root" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "ANY /api"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
  target             = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  tags = {
    Name        = "${var.name_prefix}-${var.environment}-stage-default"
    Environment = var.environment
  }
}

output "apigateway_endpoint" {
  value       = aws_apigatewayv2_api.http.api_endpoint
  description = "HTTP API Gateway endpoint URL"
}
