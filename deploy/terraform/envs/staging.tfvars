environment          = "staging"
app_env_setting      = "staging"
# Leave root_domain and route53_zone_id empty for default CloudFront deployment without Route 53.
# To enable a custom domain later (e.g. runs-on.dev), set root_domain and route53_zone_id.
root_domain          = ""
route53_zone_id      = ""
frontend_subdomain   = "app.staging"
api_subdomain        = "api.staging"
api_instance_type    = "t3.small"
worker_instance_type = "t3.small"
db_instance_class    = "db.t4g.micro"
db_multi_az          = false
db_backup_retention_days = 0
enable_cloudfront        = false
external_frontend_url    = "https://personal-os-staging.vercel.app"
admin_office_cidr        = "118.95.161.132/32"
alert_email              = ""
extra_callback_urls      = [
  "http://localhost:8080/auth/callback",
  "https://staging.d1gbedl9yrj8s.amplifyapp.com/auth/callback"
]
extra_logout_urls        = [
  "http://localhost:8080/",
  "https://staging.d1gbedl9yrj8s.amplifyapp.com/"
]
