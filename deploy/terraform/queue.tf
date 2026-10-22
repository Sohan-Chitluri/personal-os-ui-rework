# Durable capture delivery. The database/outbox remains authoritative; SQS is
# an at-least-once transport with a redrive queue for poison messages.
resource "aws_sqs_queue" "capture_dlq" {
  name                      = "${var.name_prefix}-${var.environment}-capture-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true

  tags = {
    Name        = "${var.name_prefix}-${var.environment}-capture-dlq"
    Environment = var.environment
  }
}

resource "aws_sqs_queue" "capture" {
  name                       = "${var.name_prefix}-${var.environment}-capture"
  visibility_timeout_seconds = 900
  receive_wait_time_seconds  = 20
  message_retention_seconds  = 1209600
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.capture_dlq.arn
    maxReceiveCount     = 5
  })

  tags = {
    Name        = "${var.name_prefix}-${var.environment}-capture"
    Environment = var.environment
  }
}
