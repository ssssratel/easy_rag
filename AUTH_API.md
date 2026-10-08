# 外部用户登录校验接口

完整接口文档见 [API.md](API.md)。

安装 `requirements-api.txt` 后，运行 `python api.py --port 8080` 或 `uvicorn api:app --host 0.0.0.0 --port 8080`。HTTP 接口和页面均由 `api.py` 提供；`rag_query.py` 保留命令行问答。启动 Web 服务后，请求 `POST /api/v1/auth/login`，请求体为 JSON：

```json
{"userid": "1", "token": "外部系统颁发的 token"}
```

配置环境变量 `JAVA_AUTH_VERIFY_URL`，指向 Java 身份服务的固定校验地址。认证模块只把收到的 token 以 `POST` JSON `{"token": "..."}` 发送给 Java 服务。Java 服务须返回：

- `200` 和 `{"valid": true, "userid": "1"}`：token 有效；Python 将 Java 返回的 userid 与外部请求中的 userid 比对，一致才允许登录。
- `200` 和 `{"valid": false}`，或 `401` / `403`：认证失败。
- 其他 HTTP 错误、响应格式错误或连接失败：认证服务不可用，登录接口返回 `503`。

`userid` 必须是 JSON 字符串；Java 校验服务也必须返回字符串 `userid`。数字 `1` 会被拒绝。

登录接口成功时返回 `200` 和 `{"authenticated": true, "userid": "1"}`；它不回传 token，也不创建本地会话。未配置校验地址时始终返回 `503`，不会仅凭客户端提供的 userid 和 token 判定登录成功。

请求示例：

```bash
export JAVA_AUTH_VERIFY_URL='https://your-auth-service.example/api/v1/tokens/verify'
python api.py --port 8080
curl -X POST http://localhost:8080/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"userid":"1","token":"YOUR_TOKEN"}'
```

部署时应使用受信任的 HTTPS 校验地址。外部客户端创建、删除集合及删除文件时须提供 Bearer token，服务逐次通过 Java 服务核对用户身份。用户 `1` 的网页测试会话也可执行这些操作。当前 `/ask`、`/ask_stream` 和文件上传接口尚未接入认证。

Web 首页 `/`（或 `/login.html`）提供一键测试登录。按钮请求 `POST /api/v1/auth/demo-login`，服务设置限于用户 `1` 的 HttpOnly 测试会话 Cookie，然后进入 `/rag_web.html`。该会话可创建和删除用户 `1` 的集合，无需在页面填写 Token；其他用户的集合操作仍须使用有效 Bearer Token。测试登录无需凭证，因此公开部署时须限制此入口的访问。
