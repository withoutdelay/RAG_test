from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config import get_settings
from app.services.auth import authenticate_credentials, create_session_token, verify_session_token


class AuthServiceTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_credentials_issue_and_verify_session_token(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTH_ENABLED": "true",
                "AUTH_USERNAME": "admin",
                "AUTH_PASSWORD": "secret-pass",
                "AUTH_PASSWORD_HASH": "",
                "AUTH_SESSION_TTL_SECONDS": "3600",
                "SECRET_KEY": "test-secret",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            user = authenticate_credentials("admin", "secret-pass")
            self.assertIsNotNone(user)
            assert user is not None
            token = create_session_token(user)
            verified = verify_session_token(token)

        self.assertIsNotNone(verified)
        assert verified is not None
        self.assertEqual(verified.username, "admin")

    def test_invalid_password_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTH_ENABLED": "true",
                "AUTH_USERNAME": "admin",
                "AUTH_PASSWORD": "secret-pass",
                "AUTH_PASSWORD_HASH": "",
                "SECRET_KEY": "test-secret",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            self.assertIsNone(authenticate_credentials("admin", "wrong-pass"))

    def test_tampered_session_token_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTH_ENABLED": "true",
                "AUTH_USERNAME": "admin",
                "AUTH_PASSWORD": "secret-pass",
                "AUTH_PASSWORD_HASH": "",
                "SECRET_KEY": "test-secret",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            user = authenticate_credentials("admin", "secret-pass")
            assert user is not None
            token = create_session_token(user)
            tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
            self.assertIsNone(verify_session_token(tampered))


if __name__ == "__main__":
    unittest.main()

