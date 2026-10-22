environment          = "dev"
app_env_setting      = "staging"
# Leave root_domain and route53_zone_id empty for default CloudFront deployment without Route 53.
# To enable a custom domain later, set root_domain and route53_zone_id.
root_domain          = ""
route53_zone_id      = ""
frontend_subdomain   = "app.dev"
api_subdomain        = "api.dev"
api_instance_type    = "t3.small"
worker_instance_type = "t3.small"
db_instance_class    = "db.t4g.micro"
db_multi_az          = false
alert_email          = ""
