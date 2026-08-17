#!/usr/bin/env python3
"""Serve the GIF maker on your network so your phone can open it.

    python3 gifmaker/serve.py

It prints a http://<your-ip>:8420/ address; type that into your phone's browser
while both devices are on the same wi-fi, then use "Add to Home Screen".

Static files are the whole app -- this script only adds two things a browser
cannot do on its own:

  /api/search   run an image search server-side, for when a provider's CORS
                headers block the browser from calling it directly
  /api/fetch    download one picture and hand it back with permissive CORS,
                so it can go into a <canvas> without tainting it

Only stdlib. Nothing to install.
"""

import argparse
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))

# The proxy is deliberately not general purpose. It sits on your home network
# with no password, so it will only fetch from the handful of hosts the search
# providers actually serve pictures from.
ALLOWED_HOSTS = {
    "commons.wikimedia.org",
    "upload.wikimedia.org",
    "api.openverse.org",
    "images.pexels.com",
    "www.pexels.com",
}

USER_AGENT = "gifmaker/1.0 (personal GIF maker; stdlib urllib)"

MAX_IMAGE_BYTES = 12 * 1024 * 1024


def http_get(url, headers=None, timeout=20):
    request = urllib.request.Request(url, headers=dict(headers or {}, **{
        "User-Agent": USER_AGENT,
    }))
    return urllib.request.urlopen(request, timeout=timeout)


def search_commons(query):
    params = urllib.parse.urlencode({
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": "filetype:bitmap " + query,
        "gsrnamespace": "6",
        "gsrlimit": "40",
        "prop": "imageinfo",
        "iiprop": "url|mime|extmetadata",
        "iiurlwidth": "320",
    })
    with http_get("https://commons.wikimedia.org/w/api.php?" + params) as response:
        data = json.load(response)

    results = []
    for page_id, page in (data.get("query", {}).get("pages", {}) or {}).items():
        info = (page.get("imageinfo") or [None])[0]
        if not info or not info.get("thumburl"):
            continue
        if info.get("mime") not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            continue
        meta = info.get("extmetadata") or {}
        licence = (meta.get("LicenseShortName") or {}).get("value") or ""
        thumb = info["thumburl"]
        results.append({
            "id": "commons-%s" % page_id,
            "title": (page.get("title") or "").replace("File:", ""),
            "thumb": thumb,
            "full": _commons_at_width(thumb, 1024),
            "credit": licence,
            "link": info.get("descriptionurl", ""),
        })
    return results


_THUMB_WIDTH_RE = re.compile(r"/(\d+)px-([^/]*)$")


def _commons_at_width(url, width):
    """Rewrite ".../320px-Name.jpg" to a larger rendition of the same file."""
    return _THUMB_WIDTH_RE.sub(lambda m: "/%dpx-%s" % (width, m.group(2)), url)


def search_openverse(query):
    params = urllib.parse.urlencode({"q": query, "page_size": "40", "mature": "false"})
    with http_get("https://api.openverse.org/v1/images/?" + params) as response:
        data = json.load(response)

    results = []
    for item in data.get("results", []):
        thumb = item.get("thumbnail") or item.get("url")
        if not thumb:
            continue
        credit = " · ".join(x for x in [
            item.get("creator"), (item.get("license") or "").upper()
        ] if x)
        results.append({
            "id": "openverse-%s" % item.get("id"),
            "title": item.get("title") or "Untitled",
            "thumb": thumb,
            "full": thumb,
            "credit": credit,
            "link": item.get("foreign_landing_url", ""),
        })
    return results


SEARCHERS = {"commons": search_commons, "openverse": search_openverse}


class Handler(SimpleHTTPRequestHandler):
    # Serve the app directory regardless of where the script was run from.
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def end_headers(self):
        # The app is a single origin, but the service worker and IndexedDB are
        # happier with these set explicitly, and it keeps stale files away
        # while you are editing.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/health":
            return self._json({"ok": True})
        if parsed.path == "/api/search":
            return self._search(urllib.parse.parse_qs(parsed.query))
        if parsed.path == "/api/fetch":
            return self._fetch(urllib.parse.parse_qs(parsed.query))
        return super().do_GET()

    # ------------------------------------------------------------- helpers

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _search(self, query):
        provider = (query.get("provider") or ["commons"])[0]
        term = (query.get("q") or [""])[0].strip()
        if not term:
            return self._json({"results": []})
        searcher = SEARCHERS.get(provider)
        if not searcher:
            return self._json({"error": "unknown provider %r" % provider}, 400)
        try:
            return self._json({"results": searcher(term)})
        except (urllib.error.URLError, ValueError, KeyError) as exc:
            return self._json({"error": "search failed: %s" % exc}, 502)

    def _fetch(self, query):
        target = (query.get("url") or [""])[0]
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme not in ("http", "https"):
            return self._json({"error": "only http(s) URLs"}, 400)
        if parsed.hostname not in ALLOWED_HOSTS:
            return self._json({"error": "host not allowed: %s" % parsed.hostname}, 403)

        try:
            with http_get(target) as response:
                content_type = response.headers.get("Content-Type", "")
                if not content_type.startswith("image/"):
                    return self._json({"error": "that URL is not an image"}, 415)
                body = response.read(MAX_IMAGE_BYTES + 1)
        except urllib.error.URLError as exc:
            return self._json({"error": "download failed: %s" % exc}, 502)

        if len(body) > MAX_IMAGE_BYTES:
            return self._json({"error": "that image is too big"}, 413)

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)


def lan_address():
    """Best guess at the address a phone on the same wi-fi should use."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))  # no packets are actually sent
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("GIF Maker is running.")
    print("  On this computer:  http://127.0.0.1:%d/" % args.port)
    print("  On your phone:     http://%s:%d/" % (lan_address(), args.port))
    print("\nBoth devices need to be on the same wi-fi. Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
