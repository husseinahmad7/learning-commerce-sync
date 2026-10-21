# Rate limiting in nginx

inside its http block:

    limit_req_zone $binary_remote_addr zone=checkout:10m rate=5r/s;
    server {
        listen 127.0.0.1:8088;
        server_name localhost;
        location = /api/checkout/ {
            limit_req zone=checkout burst=10 nodelay;
            limit_req_status 429;
            proxy_set_header Host localhost;
            proxy_pass http://127.0.0.1:18000;
        }
    }
