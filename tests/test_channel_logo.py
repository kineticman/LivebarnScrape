import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault(
    "DB_PATH",
    str(Path(tempfile.gettempdir()) / "livebarn-manager-tests" / "livebarn.db"),
)

import livebarn_manager


FAVORITE = {
    "surface_id": "123",
    "venue_name": "Example Ice Center",
    "surface_name": "Rink 1",
    "city": "Columbus",
    "state": "OH",
    "preferred_feed_mode": "default",
}


class ChannelLogoTests(unittest.TestCase):
    def test_logo_url_uses_github_pages(self):
        self.assertEqual(
            livebarn_manager.get_default_channel_logo_url(),
            "https://kineticman.github.io/LivebarnScrape/assets/"
            "livebarn-hockey-logo.png",
        )

    def test_playlist_emits_project_hockey_logo(self):
        with patch.object(livebarn_manager, "get_all_favorites", return_value=[FAVORITE]):
            response = livebarn_manager.app.test_client().get("/playlist.m3u")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f'tvg-logo="{livebarn_manager.get_default_channel_logo_url()}"',
            response.get_data(as_text=True),
        )

    def test_xmltv_emits_project_hockey_logo(self):
        with patch.object(livebarn_manager, "get_all_favorites", return_value=[FAVORITE]):
            response = livebarn_manager.app.test_client().get("/xmltv")

        channel = ET.fromstring(response.data).find("channel")
        self.assertIsNotNone(channel)
        self.assertEqual(
            channel.find("icon").get("src"),
            livebarn_manager.get_default_channel_logo_url(),
        )

    def test_project_hockey_logo_is_served(self):
        response = livebarn_manager.app.test_client().get(
            "/static/livebarn-hockey-logo.png"
        )
        self.addCleanup(response.close)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")


if __name__ == "__main__":
    unittest.main()
