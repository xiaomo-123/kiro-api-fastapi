# 负载均衡配置使用指南

## 方案概述

这个负载均衡方案通过Nginx实现多实例负载分发，每个实例使用独立的端口和数据库，确保高可用性和可扩展性。

## 方案特点

1. **多实例负载均衡**：通过Nginx实现请求分发，提高系统处理能力
2. **独立数据库**：每个实例使用独立的数据库文件，避免数据冲突
3. **灵活配置**：可根据需要自定义端口范围和实例数量
4. **环境变量配置**：使用环境变量动态配置端口，便于扩展

## 使用步骤

### 1. 生成配置文件

```bash
# 使用默认值（5431-5433，3个实例）
./generate-loadbalance.sh

# 自定义端口范围（5431-5435，5个实例）
./generate-loadbalance.sh 5431 5435

# 自定义端口范围（6000-6002，3个实例）
./generate-loadbalance.sh 6000 6002
```

### 2. 启动服务

```bash
# 启动所有服务
docker-compose -f docker-compose-loadbalance.yml up -d

# 查看服务状态
docker-compose -f docker-compose-loadbalance.yml ps
```

### 3. 访问服务

- **负载均衡入口**：http://38.46.219.171:5430
- **直接访问实例**：
  - 实例1：http://localhost:5431
  - 实例2：http://localhost:5432
  - 实例3：http://localhost:5433
  - （根据配置的端口范围继续递增）

### 4. 查看日志

```bash
# 查看所有服务日志
docker-compose -f docker-compose-loadbalance.yml logs

# 查看特定服务日志
docker-compose -f docker-compose-loadbalance.yml logs nginx
docker-compose -f docker-compose-loadbalance.yml logs kiro-api-1

# 实时跟踪日志
docker-compose -f docker-compose-loadbalance.yml logs -f kiro-api-1

# 查看最近N行日志
docker-compose -f docker-compose-loadbalance.yml logs --tail=100 kiro-api-1
```

### 5. 停止服务

```bash
# 停止所有服务
docker-compose -f docker-compose-loadbalance.yml down

# 停止并删除所有数据卷（包括数据库）
docker-compose -f docker-compose-loadbalance.yml down -v
```

## 重新构建服务

如果修改了代码或配置，需要重新构建服务：

```bash
docker-compose -f docker-compose-loadbalance.yml up -d --build
```

## 方案验证

1. **多端口多实例**：每个实例使用不同的端口，从起始端口开始递增
2. **负载均衡**：通过Nginx实现请求分发，采用轮询策略
3. **独立数据库**：每个实例使用独立的数据库文件（kiro_management_1.db、kiro_management_2.db等）
4. **环境变量**：使用SERVER_PORT环境变量动态配置端口

## 注意事项

1. 确保端口范围内没有其他服务占用
2. 修改端口范围后，需要先停止现有服务，重新生成配置，再启动新服务
3. 每个实例使用独立的数据库文件，数据不会自动同步
4. 静态文件目录由所有实例共享
