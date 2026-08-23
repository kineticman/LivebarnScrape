import base64
import json
import tempfile
import time
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from livebarn_api import LiveBarnClient, create_dpop_proof, first_playlist_url


def decode_segment(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class LiveBarnApiTests(unittest.TestCase):
    def test_dpop_proof_contains_request_and_valid_signature(self):
        key = ec.generate_private_key(ec.SECP256R1())
        proof = create_dpop_proof(
            key,
            "get",
            "https://example.test/resource",
            now=1234567890,
        )
        protected, payload, signature = proof.split(".")
        claims = json.loads(decode_segment(payload))
        raw_signature = decode_segment(signature)
        der_signature = encode_dss_signature(
            int.from_bytes(raw_signature[:32], "big"),
            int.from_bytes(raw_signature[32:], "big"),
        )

        self.assertEqual(claims["htm"], "GET")
        self.assertEqual(claims["htu"], "https://example.test/resource")
        self.assertEqual(claims["iat"], 1234567890)
        key.public_key().verify(
            der_signature,
            f"{protected}.{payload}".encode("ascii"),
            ec.ECDSA(hashes.SHA256()),
        )

    def test_cached_session_is_bound_to_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "livebarn.db"
            credentials = {"email": "user@example.com", "password": "secret"}
            writer = LiveBarnClient(db_path)
            writer.key = ec.generate_private_key(ec.SECP256R1())
            writer.access_token = "access-token"
            writer.user_id = "123"
            writer._save_session(credentials, time.time() + 3600)

            reader = LiveBarnClient(db_path)
            self.assertTrue(reader._load_cached_session(credentials))
            self.assertEqual(reader.access_token, "access-token")
            self.assertEqual(reader.user_id, "123")
            self.assertFalse(
                reader._load_cached_session(
                    {"email": "user@example.com", "password": "changed"}
                )
            )

    def test_first_playlist_url_resolves_relative_child(self):
        result = first_playlist_url(
            "https://cdn.example/master.m3u8?token=abc",
            "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nvideo/chunklist.m3u8\n",
        )
        self.assertEqual(result, "https://cdn.example/video/chunklist.m3u8")


if __name__ == "__main__":
    unittest.main()
