"""REST login request handling with a Java token verification service.

The Java service accepts POST JSON {"token": ...} and returns
{"valid": true, "userid": ...} for a valid token.
"""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MAX_LOGIN_BODY_BYTES = 8192
MAX_USERID_LENGTH = 128
MAX_TOKEN_LENGTH = 4096
MAX_VERIFY_RESPONSE_BYTES = 8192


class AuthUnavailable(Exception):
    """The external authentication service cannot verify this request."""


def _parse_login_body(body):
    """解析并校验登录请求中的 userid 与 token。"""
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("请求体必须是 JSON")

    if not isinstance(data, dict):
        raise ValueError("请求体必须是 JSON 对象")
    userid = data.get("userid")
    token = data.get("token")
    if not isinstance(userid, str) or not userid.strip():
        raise ValueError("userid 不能为空")
    if not isinstance(token, str) or not token.strip():
        raise ValueError("token 不能为空")
    if len(userid) > MAX_USERID_LENGTH or len(token) > MAX_TOKEN_LENGTH:
        raise ValueError("userid 或 token 超过长度限制")
    return userid.strip(), token


def verify_token_with_java(token):
    """Return the verified userid, or None if Java rejects the token."""
    verify_url = os.environ.get("JAVA_AUTH_VERIFY_URL", "").strip()
    if not verify_url:
        raise AuthUnavailable("JAVA_AUTH_VERIFY_URL 未配置")
    if not verify_url.startswith(("https://", "http://")):
        raise AuthUnavailable("JAVA_AUTH_VERIFY_URL 必须是 HTTP(S) 地址")

    payload = json.dumps({"token": token}).encode("utf-8")
    request = Request(
        verify_url,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            raw = response.read(MAX_VERIFY_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code in (401, 403):
            return None
        raise AuthUnavailable("认证服务返回错误") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise AuthUnavailable("认证服务不可用") from exc

    if len(raw) > MAX_VERIFY_RESPONSE_BYTES:
        raise AuthUnavailable("认证服务响应过大")
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthUnavailable("认证服务响应无效") from exc
    if not isinstance(result, dict) or type(result.get("valid")) is not bool:
        raise AuthUnavailable("认证服务响应无效")
    if not result["valid"]:
        return None
    verified_userid = result.get("userid")
    if not isinstance(verified_userid, str) or not verified_userid.strip():
        raise AuthUnavailable("认证服务未返回有效 userid")
    return verified_userid


def login(body):
    """Return (HTTP status, JSON payload) without exposing the token."""
    if len(body) > MAX_LOGIN_BODY_BYTES:
        return 413, {"error": "请求体过大"}
    try:
        userid, token = _parse_login_body(body)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    try:
        verified_userid = verify_token_with_java(token)
    except AuthUnavailable:
        return 503, {"error": "认证服务暂不可用"}
    if verified_userid != userid:
        return 401, {"error": "userid 或 token 无效"}
    return 200, {"authenticated": True, "userid": userid}
