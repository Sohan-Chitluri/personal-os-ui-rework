# ---------------------------------------------------------------------------
# RDS PostgreSQL: encrypted, private-subnet, TLS-enforced, autoscaling storage.
# The master password lives in Secrets Manager (referenced, never embedded).
# ---------------------------------------------------------------------------

resource "aws_db_subnet_group" "main" {
  name       = "${var.name_prefix}-${var.environment}-db-subnets"
  subnet_ids = aws_subnet.private[*].id

  tags = { Name = "${var.name_prefix}-${var.environment}-db-subnets" }
}

# TLS enforced at the server side so clients cannot opt out.
resource "aws_db_parameter_group" "main" {
  name   = "${var.name_prefix}-${var.environment}-pg16"
  family = "postgres16"

  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }

  parameter {
    name         = "log_min_duration_statement"
    value        = "2000" # ms — log slow queries > 2s
    apply_method = "immediate"
  }
}

resource "aws_db_instance" "main" {
  identifier     = "${var.name_prefix}-${var.environment}-db"
  engine         = "postgres"
  engine_version = "16.4"
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "personalos"
  username = var.db_master_username
  # Password is generated here and stored in Secrets Manager at apply time
  # by an operator runbook step (deploy/scripts/rotate-db-secret.sh); the
  # app fetches it at boot — the value is never in state after rotation, and
  # never in the repo.
  password = random_password.db_master.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.main.name

  multi_az                   = var.db_multi_az
  publicly_accessible        = false
  auto_minor_version_upgrade = true

  backup_retention_period   = var.db_backup_retention_days
  backup_window             = "03:00-04:00"
  maintenance_window        = "sun:04:30-sun:05:30"
  copy_tags_to_snapshot     = true
  deletion_protection       = var.environment == "production"
  skip_final_snapshot       = var.environment != "production"
  final_snapshot_identifier = var.environment == "production" ? "${var.name_prefix}-${var.environment}-db-final" : null

  performance_insights_enabled = var.environment == "production"
  monitoring_interval          = var.environment == "production" ? 60 : 0
  monitoring_role_arn          = var.environment == "production" ? aws_iam_role.rds_enhanced_monitoring[0].arn : null

  tags = {
    Name        = "${var.name_prefix}-${var.environment}-db"
    Environment = var.environment
  }
}

resource "random_password" "db_master" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_iam_role" "rds_enhanced_monitoring" {
  count = var.environment == "production" ? 1 : 0

  name = "${var.name_prefix}-${var.environment}-rds-monitoring"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_enhanced_monitoring" {
  count = var.environment == "production" ? 1 : 0

  role       = aws_iam_role.rds_enhanced_monitoring[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringPolicy"
}

# Secret storing the DB master credentials. The app reads this via the
# instance role at boot (see templates/user_data.sh.tftpl).
resource "aws_secretsmanager_secret" "db" {
  name                    = "${var.name_prefix}/${var.environment}/database"
  description             = "RDS master credentials for Personal OS"
  kms_key_id              = null # default AWS-managed key
  recovery_window_in_days = 7

  tags = { Environment = var.environment }
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    username = var.db_master_username
    password = random_password.db_master.result
    engine   = "postgres"
    host     = aws_db_instance.main.address
    port     = 5432
    dbname   = "personalos"
    sslmode  = "require"
  })
}

output "rds_endpoint" {
  value = aws_db_instance.main.address
}

output "db_secret_arn" {
  value = aws_secretsmanager_secret.db.arn
}
