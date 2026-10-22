environment          = "production"
app_env_setting      = "production"
# Apex domain and Route 53 zone ID for production custom domain.
# Leave empty if deploying against the default CloudFront domain.
root_domain          = ""
route53_zone_id      = ""
frontend_subdomain   = "app"
api_subdomain        = "api"
api_instance_type    = "t3.medium"
worker_instance_type = "t3.medium"
db_instance_class    = "db.t4g.small"
db_multi_az          = true
alert_email          = ""
