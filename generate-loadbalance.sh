#!/bin/bash

START_PORT=${1:-5432}
END_PORT=${2:-5435}
INSTANCES=$((END_PORT - START_PORT + 1))

cat > Dockerfile.lb << 'EOF'
FROM python:3.10.6-slim
WORKDIR /app
RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY main.py .
EXPOSE ${SERVER_PORT}
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port \${SERVER_PORT}"
EOF

cat > nginx.conf << EOF
events { worker_connections 1024; }
http {
    upstream kiro_api_backend {
EOF
for ((i=1; i<=INSTANCES; i++)); do
    echo "        server kiro-api-$i:$((START_PORT + i - 1));" >> nginx.conf
done
cat >> nginx.conf << 'EOF'
    }
    server {
        listen 80;
        location / {
            proxy_pass http://kiro_api_backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_connect_timeout 60s;
            proxy_send_timeout 60s;
            proxy_read_timeout 60s;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
        }
    }
}
EOF

cat > docker-compose-loadbalance.yml << 'EOF'
version: '3.8'
services:
  nginx:
    image: nginx:latest
    container_name: kiro-api-nginx
    ports:
      - "5430:80"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
    depends_on:
EOF
for ((i=1; i<=INSTANCES; i++)); do
    echo "      - kiro-api-$i" >> docker-compose-loadbalance.yml
done
cat >> docker-compose-loadbalance.yml << 'EOF'
    restart: unless-stopped
EOF

for ((i=1; i<=INSTANCES; i++)); do
    PORT=$((START_PORT + i - 1))
    cat >> docker-compose-loadbalance.yml << EOF
  kiro-api-$i:
    build:
      context: .
      dockerfile: Dockerfile.lb
    container_name: kiro-api-fastapi-$i
    ports:
      - "$PORT:$PORT"
    volumes:
      - ./app/static:/app/app/static
      - ./kiro_management_$i.db:/app/kiro_management.db
      - ./.env:/app/.env
    environment:
      - HOST=0.0.0.0
      - SERVER_PORT=$PORT
      - REQUIRED_API_KEY=123456
    restart: unless-stopped
EOF
done

echo "配置文件生成完成！"
echo "  - $INSTANCES 个服务实例"
echo "  - 端口范围: $START_PORT 到 $END_PORT"
