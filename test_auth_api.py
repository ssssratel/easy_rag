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
        response = io.BytesIO(b'{"valid": true, "userid": "alice"}')
        with patch.object(auth_api, "urlopen", return_value=response) as call:
            status, payload = auth_api.login(b'{"userid":"alice","token":"opaque-token"}')
        self.assertEqual((status, payload), (200, {"authenticated": True, "userid": "alice"}))
        sent = json.loads(call.call_args.args[0].data)
        self.assertEqual(sent, {"token": "opaque-token"})
        self.assertNotIn("token", payload)

    def test_mismatched_user_is_rejected(self):
        """拒绝与 Java 返回身份不一致的 userid。"""
        response = io.BytesIO(b'{"valid": true, "userid": "bob"}')
        with patch.object(auth_api, "urlopen", return_value=response):
            status, _ = auth_api.login(b'{"userid":"alice","token":"opaque-token"}')
        self.assertEqual(status, 401)

    def test_invalid_token_is_rejected(self):
        """将 Java 的未授权响应转换为登录失败。"""
        with patch.object(auth_api, "urlopen", side_effect=HTTPError(
            "https://auth.example.test/verify", 401, "Unauthorized", {}, None
        )):
            status, _ = auth_api.login(b'{"userid":"alice","token":"bad"}')
        self.assertEqual(status, 401)

    def test_missing_verifier_fails_closed(self):
        """未配置校验地址时拒绝登录。"""
        os.environ.pop("JAVA_AUTH_VERIFY_URL")
        status, _ = auth_api.login(b'{"userid":"alice","token":"opaque-token"}')
        self.assertEqual(status, 503)

    def test_invalid_request_and_verifier_response(self):
        """拒绝不完整请求和无效的校验服务响应。"""
        self.assertEqual(auth_api.login(b'{"userid":"alice"}')[0], 400)
        with patch.object(auth_api, "urlopen", return_value=io.BytesIO(b'{}')):
            status, _ = auth_api.login(b'{"userid":"alice","token":"opaque-token"}')
        self.assertEqual(status, 503)


if __name__ == "__main__":
    unittest.main()
