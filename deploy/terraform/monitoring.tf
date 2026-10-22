# ---------------------------------------------------------------------------
# CloudWatch: log groups, alarms, and SNS topic for operational alerts.
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name = "${var.name_prefix}-${var.environment}-alerts"

  tags = { Environment = var.environment }
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# --- Log groups -------------------------------------------------------------

resource "aws_cloudwatch_log_group" "api" {
  name              = "/personal-os/${var.environment}/api"
  retention_in_days = var.log_retention_days
  kms_key_id        = null # default CMK

  tags = { Environment = var.environment }
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/personal-os/${var.environment}/worker"
  retention_in_days = var.log_retention_days

  tags = { Environment = var.environment }
}

# The CloudWatch agent on the instances ships journald → these groups; unit
# files set SyslogIdentifier=personal-os-api / personal-os-worker.
resource "aws_ssm_parameter" "cw_agent_config" {
  name = "/personal-os/${var.environment}/cloudwatch-agent-config"
  type = "String"
  value = jsonencode({
    logs = {
      logs_collected = {
        journald = {
          collect_list = [
            {
              log_group_name  = aws_cloudwatch_log_group.api.name
              log_stream_name = "{instance_id}/api"
            }
          ]
        }
      }
    }
    metrics = {
      namespace = "PersonalOS/${var.environment}"
      append_dimensions = {
        InstanceId = "$${InstanceId}"
      }
      metrics_collected = {
        mem = {
          measurement = ["mem_used_percent"]
        }
        swap = {
          measurement = ["swap_used_percent"]
        }
      }
    }
  })

  tags = { Environment = var.environment }
}

# --- Alarms (minimum useful set) -------------------------------------------

# 1. API healthy hosts: alarm when no API target is healthy. UnHealthyHostCount
# counts individual failed targets and would alarm while a redundant API host
# remains healthy, so use HealthyHostCount and treat missing data as downtime.
resource "aws_cloudwatch_metric_alarm" "api_unhealthy" {
  alarm_name          = "${var.name_prefix}-${var.environment}-api-unhealthy-hosts"
  alarm_description   = "API target group has zero healthy hosts — service down"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  dimensions          = { TargetGroup = aws_lb_target_group.api.arn_suffix, LoadBalancer = aws_lb.main.arn_suffix }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# 2. API 5xx rate.
resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${var.name_prefix}-${var.environment}-api-5xx"
  alarm_description   = "API returning elevated 5xx responses"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  dimensions          = { TargetGroup = aws_lb_target_group.api.arn_suffix, LoadBalancer = aws_lb.main.arn_suffix }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 20
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# 3. RDS CPU saturation.
resource "aws_cloudwatch_metric_alarm" "db_cpu" {
  alarm_name          = "${var.name_prefix}-${var.environment}-db-cpu-high"
  alarm_description   = "RDS CPU sustained high — investigate slow queries or scale"
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.id }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 70
  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# 4. RDS low storage headroom.
resource "aws_cloudwatch_metric_alarm" "db_storage" {
  alarm_name          = "${var.name_prefix}-${var.environment}-db-storage-low"
  alarm_description   = "RDS free storage below 5GB"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.id }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5000000000 # 5 GB in bytes
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# 5. Worker heartbeat staleness — the worker publishes a per-minute metric;
# silence for 5 minutes means the loop is wedged or the process died.
resource "aws_cloudwatch_metric_alarm" "worker_heartbeat" {
  alarm_name          = "${var.name_prefix}-${var.environment}-worker-heartbeat-stale"
  alarm_description   = "Worker heartbeat metric missing for 5 minutes — worker wedged or dead"
  namespace           = "PersonalOS/${var.environment}"
  metric_name         = "WorkerHeartbeat"
  statistic           = "SampleCount"
  period              = 60
  evaluation_periods  = 5
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# 6. Memory pressure on the app instance (CloudWatch agent metric).
resource "aws_cloudwatch_metric_alarm" "app_memory" {
  alarm_name          = "${var.name_prefix}-${var.environment}-app-memory-high"
  alarm_description   = "App instance memory above 85%"
  namespace           = "PersonalOS/${var.environment}"
  metric_name         = "mem_used_percent"
  dimensions          = {}
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 3
  threshold           = 85
  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# Queue backlog alerts: oldest-message age signals stalled processing; any DLQ
# message requires investigation/replay rather than silent loss.
resource "aws_cloudwatch_metric_alarm" "capture_queue_age" {
  alarm_name          = "${var.name_prefix}-${var.environment}-capture-queue-age"
  alarm_description   = "Capture queue messages are waiting too long for processing"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = aws_sqs_queue.capture.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  threshold           = 900
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "capture_dlq_messages" {
  alarm_name          = "${var.name_prefix}-${var.environment}-capture-dlq-nonempty"
  alarm_description   = "Capture messages reached the dead-letter queue and need investigation"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.capture_dlq.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
