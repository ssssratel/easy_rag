"""认证模块的输入校验与外部服务响应测试。"""

import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import auth_api


class LoginApiTests(unittest.TestCase):
    """覆盖登录成功、拒绝和服务故障场景。"""

    def setUp(self):
        """为每个测试配置模拟认证地址。"""
        self.environment = patch.dict(
            os.environ, {"JAVA_AUTH_VERIFY_URL": "https://auth.example.test/verify"}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_valid_token_and_matching_user(self):
        """有效 token 仅返回已验证的用户信息。"""
        response = io.BytesIO(b'{"valid": true, "userid": "1"}')
        with patch.object(auth_api, "urlopen", return_value=response) as call:
            status, payload = auth_api.login(b'{"userid":"1","token":"opaque-token"}')
        self.assertEqual((status, payload), (200, {"authenticated": True, "userid": "1"}))
        sent = json.loads(call.call_args.args[0].data)
        self.assertEqual(sent, {"token": "opaque-token"})
        self.assertNotIn("token", payload)

    def test_mismatched_user_is_rejected(self):
        """拒绝与 Java 返回身份不一致的 userid。"""
        response = io.BytesIO(b'{"valid": true, "userid": "2"}')
        with patch.object(auth_api, "urlopen", return_value=response):
            status, _ = auth_api.login(b'{"userid":"1","token":"opaque-token"}')
        self.assertEqual(status, 401)

    def test_invalid_token_is_rejected(self):
        """将 Java 的未授权响应转换为登录失败。"""
        with patch.object(auth_api, "urlopen", side_effect=HTTPError(
            "https://auth.example.test/verify", 401, "Unauthorized", {}, None
        )):
            status, _ = auth_api.login(b'{"userid":"1","token":"bad"}')
        self.assertEqual(status, 401)

    def test_missing_verifier_fails_closed(self):
        """未配置校验地址时拒绝登录。"""
        os.environ.pop("JAVA_AUTH_VERIFY_URL")
        status, _ = auth_api.login(b'{"userid":"1","token":"opaque-token"}')
        self.assertEqual(status, 503)

    def test_invalid_request_and_verifier_response(self):
        """拒绝不完整请求和无效的校验服务响应。"""
        self.assertEqual(auth_api.login(b'{"userid":"1"}')[0], 400)
        with patch.object(auth_api, "urlopen", return_value=io.BytesIO(b'{}')):
            status, _ = auth_api.login(b'{"userid":"1","token":"opaque-token"}')
        self.assertEqual(status, 503)

    def test_rejects_numeric_userid_in_login_or_verifier(self):
        self.assertEqual(auth_api.login(b'{"userid":1,"token":"opaque-token"}')[0], 400)
        with patch.object(auth_api, "urlopen", return_value=io.BytesIO(
            b'{"valid": true, "userid": 1}'
        )):
            self.assertEqual(auth_api.login(b'{"userid":"1","token":"opaque-token"}')[0], 503)

    def test_collection_bearer_authorizes_only_matching_user(self):
        with patch.object(auth_api, "verify_token_with_java", return_value="1"):
            self.assertEqual(auth_api.authorize_bearer("Bearer valid", "1"), 200)
            self.assertEqual(auth_api.authorize_bearer("Bearer valid", "2"), 403)
        self.assertEqual(auth_api.authorize_bearer(None, "1"), 401)
        self.assertEqual(auth_api.authorize_bearer("Bearer ", "1"), 401)
        with patch.object(auth_api, "verify_token_with_java",
                          side_effect=auth_api.AuthUnavailable()):
            self.assertEqual(auth_api.authorize_bearer("Bearer valid", "1"), 503)


if __name__ == "__main__":
    unittest.main()
