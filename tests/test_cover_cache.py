import http.server
import socketserver
import tempfile
import threading
import unittest
from functools import partial
from pathlib import Path


class _ImageHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/cover.webp":
            body = b"fake-webp-bytes"
            self.send_response(200)
            self.send_header("Content-Type", "image/webp")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(403)
        self.end_headers()

    def log_message(self, *_):
        pass


class CoverCacheTests(unittest.TestCase):
    def _server(self):
        server = socketserver.TCPServer(("127.0.0.1", 0), _ImageHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_downloads_cover_and_returns_local_url(self):
        from crawler.cover_cache import cache_cover_image

        base = self._server()
        with tempfile.TemporaryDirectory() as tmp:
            local_url = cache_cover_image(
                "note-1",
                f"{base}/cover.webp",
                covers_dir=Path(tmp) / "covers",
            )
            self.assertEqual(local_url, "/covers/note-1.webp")
            self.assertEqual((Path(tmp) / "covers" / "note-1.webp").read_bytes(), b"fake-webp-bytes")

    def test_keeps_original_url_when_download_fails(self):
        from crawler.cover_cache import cache_cover_image

        base = self._server()
        with tempfile.TemporaryDirectory() as tmp:
            original = f"{base}/forbidden.webp"
            local_url = cache_cover_image(
                "note-1",
                original,
                covers_dir=Path(tmp) / "covers",
            )
            self.assertEqual(local_url, original)
            self.assertFalse((Path(tmp) / "covers" / "note-1.webp").exists())

    def test_reuses_existing_cached_cover(self):
        from crawler.cover_cache import cache_cover_image

        with tempfile.TemporaryDirectory() as tmp:
            covers = Path(tmp) / "covers"
            covers.mkdir()
            (covers / "note-1.webp").write_bytes(b"cached")
            local_url = cache_cover_image("note-1", "https://example.invalid/cover.webp", covers_dir=covers)
            self.assertEqual(local_url, "/covers/note-1.webp")


if __name__ == "__main__":
    unittest.main()
