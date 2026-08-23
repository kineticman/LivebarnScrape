import unittest

from hls_relay import HlsRelayError, parse_media_playlist


class HlsRelayTests(unittest.TestCase):
    def test_parses_relative_segments_and_target_duration(self):
        snapshot = parse_media_playlist(
            "https://cdn.example/live/chunklist.m3u8?token=abc",
            """#EXTM3U
#EXT-X-TARGETDURATION:8
#EXTINF:8.0,
segment-10.ts
#EXTINF:8.0,
segment-11.ts?part=1
""",
        )

        self.assertEqual(snapshot.target_duration, 8.0)
        self.assertFalse(snapshot.ended)
        self.assertEqual(
            snapshot.segment_urls,
            [
                "https://cdn.example/live/segment-10.ts",
                "https://cdn.example/live/segment-11.ts?part=1",
            ],
        )

    def test_detects_endlist(self):
        snapshot = parse_media_playlist(
            "https://cdn.example/archive.m3u8",
            "#EXTM3U\n#EXTINF:4,\nsegment.ts\n#EXT-X-ENDLIST\n",
        )
        self.assertTrue(snapshot.ended)

    def test_rejects_non_hls_body(self):
        with self.assertRaises(HlsRelayError):
            parse_media_playlist("https://cdn.example/live.m3u8", "Forbidden")


if __name__ == "__main__":
    unittest.main()
