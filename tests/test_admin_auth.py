import base64
import os
import tempfile
import unittest
from pathlib import Path


os.environ.setdefault(
    "DB_PATH",
    str(Path(tempfile.gettempdir()) / "livebarn-manager-tests" / "livebarn.db"),
)

import livebarn_manager


class AdminAuthTests(unittest.TestCase):
    def setUp(self):
        self.original_username = livebarn_manager.ADMIN_USERNAME
        self.original_password = livebarn_manager.ADMIN_PASSWORD
        livebarn_manager.ADMIN_USERNAME = "operator"
        livebarn_manager.ADMIN_PASSWORD = "test-password"

    def tearDown(self):
        livebarn_manager.ADMIN_USERNAME = self.original_username
        livebarn_manager.ADMIN_PASSWORD = self.original_password

    def test_admin_route_requires_credentials_when_enabled(self):
        with livebarn_manager.app.test_request_context("/api/favorites"):
            response = livebarn_manager.require_admin_auth()

        self.assertEqual(response.status_code, 401)
        self.assertIn("Basic", response.headers["WWW-Authenticate"])

    def test_valid_basic_credentials_allow_admin_route(self):
        encoded = base64.b64encode(b"operator:test-password").decode("ascii")
        headers = {"Authorization": f"Basic {encoded}"}
        with livebarn_manager.app.test_request_context("/", headers=headers):
            response = livebarn_manager.require_admin_auth()

        self.assertIsNone(response)

    def test_dvr_routes_remain_public(self):
        for path in ("/playlist.m3u", "/xmltv", "/proxy/123", "/health"):
            with self.subTest(path=path):
                with livebarn_manager.app.test_request_context(path):
                    response = livebarn_manager.require_admin_auth()
                self.assertIsNone(response)

    def test_empty_password_disables_admin_auth(self):
        livebarn_manager.ADMIN_PASSWORD = ""
        with livebarn_manager.app.test_request_context("/api/favorites"):
            response = livebarn_manager.require_admin_auth()

        self.assertIsNone(response)


if __name__ == "__main__":
    unittest.main()
