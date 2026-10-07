# 外部用户登录校验接口

启动现有 Web 服务后，请求 `POST /api/v1/auth/login`，请求体为 JSON：

```json
{"userid": "alice", "token": "外部系统颁发的 token"}
```

配置环境变量 `JAVA_AUTH_VERIFY_URL`，指向 Java 身份服务的固定校验地址。认证模块只把收到的 token 以 `POST` JSON `{"token": "..."}` 发送给 Java 服务。Java 服务须返回：

- `200` 和 `{"valid": true, "userid": "alice"}`：token 有效；Python 将 Java 返回的 userid 与外部请求中的 userid 比对，一致才允许登录。
- `200` 和 `{"valid": false}`，或 `401` / `403`：认证失败。
- 其他 HTTP 错误、响应格式错误或连接失败：认证服务不可用，登录接口返回 `503`。

登录接口成功时返回 `200` 和 `{"authenticated": true, "userid": "alice"}`；它不回传 token，也不创建本地会话。未配置校验地址时始终返回 `503`，不会仅凭客户端提供的 userid 和 token 判定登录成功。

请求示例：

```bash
export JAVA_AUTH_VERIFY_URL='https://your-auth-service.example/api/v1/tokens/verify'
python rag_query.py --serve
curl -X POST http://localhost:8080/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"userid":"alice","token":"YOUR_TOKEN"}'
```

部署时应使用受信任的 HTTPS 校验地址。当前 `/ask` 与 `/ask_stream` 未接入此认证接口，仍按原有方式提供服务。
