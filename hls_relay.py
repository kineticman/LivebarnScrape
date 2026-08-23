"""Small HLS relay for CDNs that require a browser TLS fingerprint."""

import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from urllib.parse import urljoin

from curl_cffi import requests


@dataclass(frozen=True)
class PlaylistSnapshot:
    segment_urls: list[str]
    target_duration: float
    ended: bool


class HlsRelayError(RuntimeError):
    """Raised when an HLS playlist or segment cannot be relayed."""


def parse_media_playlist(url: str, text: str) -> PlaylistSnapshot:
    """Parse the fields needed to poll and relay an MPEG-TS media playlist."""
    if not text.lstrip().startswith("#EXTM3U"):
        raise HlsRelayError("CDN returned an invalid HLS playlist")

    duration_match = re.search(r"^#EXT-X-TARGETDURATION:([\d.]+)", text, re.MULTILINE)
    target_duration = float(duration_match.group(1)) if duration_match else 6.0
    segment_urls = [
        urljoin(url, line.strip())
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return PlaylistSnapshot(
        segment_urls=segment_urls,
        target_duration=target_duration,
        ended="#EXT-X-ENDLIST" in text,
    )


def iter_hls_stream(
    playlist_url: str,
    logger: logging.Logger,
    chunk_size: int = 64 * 1024,
):
    """Yield live MPEG-TS bytes while polling the media playlist for segments."""
    session = requests.Session(impersonate="chrome")
    seen: set[str] = set()
    seen_order: deque[str] = deque()
    first_poll = True
    empty_polls = 0

    try:
        while True:
            try:
                playlist_response = session.get(playlist_url, timeout=30)
            except Exception as exc:
                raise HlsRelayError("Unable to fetch the LiveBarn HLS playlist") from exc
            if not playlist_response.ok:
                raise HlsRelayError(
                    f"LiveBarn HLS playlist returned {playlist_response.status_code}"
                )

            snapshot = parse_media_playlist(
                str(playlist_response.url), playlist_response.text
            )
            candidates = snapshot.segment_urls[-2:] if first_poll else snapshot.segment_urls
            first_poll = False
            new_segments = [url for url in candidates if url not in seen]

            for segment_url in new_segments:
                try:
                    segment_response = session.get(
                        segment_url,
                        stream=True,
                        timeout=45,
                    )
                except Exception as exc:
                    raise HlsRelayError("Unable to fetch a LiveBarn video segment") from exc
                try:
                    if not segment_response.ok:
                        raise HlsRelayError(
                            f"LiveBarn video segment returned {segment_response.status_code}"
                        )
                    seen.add(segment_url)
                    seen_order.append(segment_url)
                    if len(seen_order) > 500:
                        seen.discard(seen_order.popleft())
                    for chunk in segment_response.iter_content(chunk_size=chunk_size):
                        if chunk:
                            yield chunk
                finally:
                    segment_response.close()

            if snapshot.ended:
                return

            if new_segments:
                empty_polls = 0
            else:
                empty_polls += 1
                if empty_polls % 12 == 0:
                    logger.debug("Waiting for the next LiveBarn HLS segment")
            time.sleep(min(max(snapshot.target_duration / 2, 1.0), 5.0))
    finally:
        session.close()
