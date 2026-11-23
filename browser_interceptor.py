"""
browser_interceptor.py — Local HTTP Listener for Browser Extensions & Clipboard
================================================================================
Provides:
  1. A lightweight HTTP daemon (port 6801) that accepts download requests
     from the Chrome / Brave / Arc browser extension.
  2. CORS headers and JSON responses for browser extension compatibility.
  3. Clipboard monitor helper for catching magnet links and download URLs.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_PORT = 6801


class InterceptorHandler(BaseHTTPRequestHandler):
    """Handles incoming requests from browser extensions or native scripts."""

    # Set by the server instance
    on_download_received: Optional[Callable[[dict], None]] = None

    def log_message(self, format, *args):
        # Silence default standard err log noise
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path in ("/ping", "/status", "/"):
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {
                "status": "online",
                "service": "hydownload managers Interceptor",
                "version": "1.0.0"
            }
            self.wfile.write(json.dumps(response).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/add":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8")
                data = json.loads(body) if body else {}

                url = data.get("url", "").strip()
                if not url:
                    self.send_response(400)
                    self._send_cors_headers()
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Missing 'url' parameter"}).encode("utf-8"))
                    return

                logger.info("Received download from browser: %s (name: %s)", url[:80], data.get("filename"))

                if InterceptorHandler.on_download_received:
                    InterceptorHandler.on_download_received(data)

                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "queued", "url": url}).encode("utf-8"))

            except Exception as e:
                logger.error("Error processing /add request: %s", e)
                self.send_response(500)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


class BrowserInterceptorServer:
    """Manages the background HTTP server lifecycle."""

    def __init__(self, port: int = DEFAULT_PORT, on_download: Optional[Callable[[dict], None]] = None):
        self.port = port
        self.on_download = on_download
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> "BrowserInterceptorServer":
        if self._running:
            return self

        InterceptorHandler.on_download_received = self.on_download
        try:
            self._server = ReusableHTTPServer(("127.0.0.1", self.port), InterceptorHandler)
            self._running = True
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            logger.info("Browser interceptor listener active on http://127.0.0.1:%d", self.port)
        except OSError as e:
            logger.warning("Could not bind browser interceptor to port %d: %s", self.port, e)
            self._running = False
        return self

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        try:
            if self._server:
                self._server.shutdown()
                self._server.server_close()
        except Exception:
            pass
        logger.info("Browser interceptor listener stopped.")

    @property
    def is_running(self) -> bool:
        return self._running
