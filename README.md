# 企业 IT 运维工单 Agent

这是一个具有生产化设计的个人项目 MVP，不是已经接入真实企业 ITSM 的生产系统。

## 业务闭环

用户请求 -> 确定性安全分流 -> 资产查询或受控知识检索 -> 检索响应校验 -> 带引用建议 -> 用户明确确认 -> `pending_review` 工单草稿 -> 人工审核 -> 审计记录。

## 工程能力

- Dify 1.18 Chatflow：14 个节点、13 条连线。
- FastAPI 工具服务：知识检索、资产查询、工单草稿、人工审核和审计接口。
- API Key 鉴权、限流、Request ID、幂等去重、SQLite 持久化和 Docker 健康检查。
- 安全事件优先升级；不索取密码、验证码、MFA 动态码、私钥或访问令牌。
- LLM 只能依据工具返回的 `citation_id` 回答；无证据时不得猜测。
- 工单默认是 `pending_review`；本 MVP 不会自动提交到真实 ITSM。

## 启动与关闭

```powershell
git clone https://github.com/2Ccw/enterprise-it-ops-agent.git
cd enterprise-it-ops-agent
Copy-Item .env.example .env
# 将 IT_OPS_API_KEY 替换为本机生成的随机长字符串
docker compose up -d --build
```

- 健康检查：http://127.0.0.1:8030/health
- API 文档：http://127.0.0.1:8030/docs

关闭服务：

```powershell
docker compose down
```

## 测试

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r backend\requirements.txt
Set-Location backend
..\.venv\Scripts\python -m pytest -q tests
```

## 导入 Dify

优先导入：

```text
dify/enterprise-it-ops-ticket-agent.ifpkg
```

该应用包由 Dify 1.18 导出并完成了重新导入验证，且不包含 Secret。也可以导入源 DSL：

```text
dify/enterprise-it-ops-ticket-agent.yml
```

导入后，在工作流顶部点击“环境变量”，编辑 `IT_OPS_API_KEY`，将“变量值”设为本项目 `.env` 中的同名值。`.env` 已加入 `.gitignore`，不要将真实密钥写入DSL、提交到仓库或放进作品集。

## Dify 本地网络

Dify 1.18 默认阻止 HTTP 节点访问私网服务。本机采用最小权限 `/32` 白名单，只允许当前 `it-ops-api` 容器 IP。Docker 重启后如果容器 IP 变化，需要同步更新：

在Dify部署目录的 `docker/.env` 中设置：

```text
SSRF_PROXY_ALLOW_PRIVATE_IPS=<it-ops-api 当前 IP>/32
```

然后重建两个代理：

```powershell
cd <你的 Dify 部署目录>\docker
docker compose up -d --force-recreate ssrf_proxy agent_ssrf_proxy
```

不要直接将整个私网网段加入白名单；生产环境应使用固定网络策略、服务身份和最小权限访问控制。

## 已验证结果

- 后端自动化测试：10 项通过。
- Dify 场景测试：安全升级、资产查询、VPN 知识检索、越权安装拒绝、工单草稿，共 5 项通过。
- 导出包 Secret 扫描：通过，未发现密钥材料。
- Dify 导出再导入：通过，无“渲染此组件时发生了意外错误”。

## 生产升级边界

- SQLite 应迁移到 PostgreSQL，单实例内存限流应迁移到 Redis。
- 演示知识检索应升级为 Embedding + Rerank，并评估召回率和引用正确率。
- 接入真实 ITSM 前必须增加企业 SSO、RBAC、审批策略、数据保留策略和安全评审。
