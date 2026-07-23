import os
import unittest
from unittest.mock import patch

from ps_bridge.security import (
    ALLOW_LAN_ENV,
    AUTH_TOKEN_ENV,
    auth_token_matches,
    bearer_token,
    is_loopback_ip,
    lan_access_enabled,
    lan_access_ready,
)


class BridgeNetworkSecurityTests(unittest.TestCase):
    def test_loopback_detection(self):
        self.assertTrue(is_loopback_ip("127.0.0.1"))
        self.assertTrue(is_loopback_ip("::1"))
        self.assertFalse(is_loopback_ip("192.168.1.25"))
        self.assertFalse(is_loopback_ip("invalid"))

    def test_lan_access_fails_closed_without_token(self):
        with patch.dict(os.environ, {ALLOW_LAN_ENV: "1"}, clear=True):
            self.assertTrue(lan_access_enabled())
            self.assertFalse(lan_access_ready())
            self.assertFalse(auth_token_matches("anything"))

    def test_lan_access_requires_matching_token(self):
        with patch.dict(
            os.environ,
            {ALLOW_LAN_ENV: "true", AUTH_TOKEN_ENV: "correct-horse-battery-staple"},
            clear=True,
        ):
            self.assertTrue(lan_access_ready())
            self.assertTrue(auth_token_matches("correct-horse-battery-staple"))
            self.assertFalse(auth_token_matches("wrong"))

    def test_bearer_token_parser(self):
        self.assertEqual(bearer_token("Bearer secret-value"), "secret-value")
        self.assertEqual(bearer_token("bearer secret-value"), "secret-value")
        self.assertEqual(bearer_token("Basic abc"), "")
        self.assertEqual(bearer_token(None), "")


if __name__ == "__main__":
    unittest.main()
