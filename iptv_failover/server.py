from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .core import (
    GroupedChannel,
    build_proxy_playlist,
    build_tvbox_txt,
    group_channels,
    index_by_slug,
    parse_m3u,
)


DEFAULT_SOURCE = "https://raw.githubusercontent.com/best-fan/iptv-sources/master/cn_all.m3u8"


class PlaylistState:
    def __init__(self, source_url: str, refresh_seconds: int = 1800, probe_timeout: float = 4.0):
        self.source_url = source_url
        self.refresh_seconds = refresh_seconds
        self.probe_timeout = probe_timeout
        self.loaded_at = 0.0
        self.groups: Dict[str, GroupedChannel] = {}
        self.by_slug: Dict[str, GroupedChannel] = {}
        self.bad_until: Dict[str, float] = {}

    def refresh_if_needed(self, force: bool = False) -> None:
        now = time.time()
        if not force and self.groups and now - self.loaded_at < self.refresh_seconds:
            return

        request = Request(self.source_url, headers={"User-Agent": "iptv-failover/1.0"})
        with urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8-sig", errors="replace")

        entries = parse_m3u(body)
        groups = group_channels(entries)
        self.groups = groups
        self.by_slug = index_by_slug(groups)
        self.loaded_at = now

    def choose_source(self, channel: GroupedChannel) -> Optional[str]:
        now = time.time()
        fallback = channel.sources[0].url if channel.sources else None

        for source in channel.sources:
            if self.bad_until.get(source.url, 0) > now:
                continue
            if self._probe(source.url):
                return source.url
            self.bad_until[source.url] = now + 60

        return fallback

    def _probe(self, url: str) -> bool:
        request = Request(
            url,
            headers={
                "User-Agent": "iptv-failover/1.0",
                "Range": "bytes=0-2047",
            },
        )
        try:
            with urlopen(request, timeout=self.probe_timeout) as response:
                status_ok = 200 <= response.status < 400
                data = response.read(256)
                return status_ok and bool(data)
        except (OSError, URLError, TimeoutError, ValueError):
            return False


def make_handler(state: PlaylistState, fixed_base_url: Optional[str] = None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "IptvFailover/1.0"

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/playlist.m3u8"}:
                self._serve_playlist()
                return
            if parsed.path == "/status.json":
                self._serve_status()
                return
            if parsed.path.startswith("/live/") and parsed.path.endswith(".m3u8"):
                slug = parsed.path.removeprefix("/live/").removesuffix(".m3u8")
                self._serve_live(slug)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_HEAD(self):
            self.do_GET()

        def log_message(self, fmt, *args):
            sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

        def _serve_playlist(self):
            try:
                state.refresh_if_needed()
            except Exception as exc:
                self.send_error(HTTPStatus.BAD_GATEWAY, f"Failed to fetch source playlist: {exc}")
                return

            base_url = fixed_base_url or f"http://{self.headers.get('Host')}"
            playlist = build_proxy_playlist(state.groups, base_url)
            self._send_text(playlist, "application/vnd.apple.mpegurl; charset=utf-8")

        def _serve_status(self):
            try:
                state.refresh_if_needed()
            except Exception as exc:
                self.send_error(HTTPStatus.BAD_GATEWAY, f"Failed to fetch source playlist: {exc}")
                return
            payload = {
                "source": state.source_url,
                "loaded_at": state.loaded_at,
                "channels": len(state.groups),
                "sources": sum(len(channel.sources) for channel in state.groups.values()),
            }
            self._send_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "application/json; charset=utf-8")

        def _serve_live(self, slug: str):
            try:
                state.refresh_if_needed()
            except Exception as exc:
                self.send_error(HTTPStatus.BAD_GATEWAY, f"Failed to fetch source playlist: {exc}")
                return

            channel = state.by_slug.get(slug)
            if channel is None:
                self.send_error(HTTPStatus.NOT_FOUND, "Unknown channel")
                return

            source_url = state.choose_source(channel)
            if not source_url:
                self.send_error(HTTPStatus.BAD_GATEWAY, "No source URL for channel")
                return

            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", source_url)
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()

        def _send_text(self, body: str, content_type: str):
            data = body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

    return Handler


def guess_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("223.5.5.5", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Serve a grouped IPTV playlist with per-channel source failover.")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Source M3U/M3U8 playlist URL")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", default=8899, type=int, help="Bind port")
    parser.add_argument("--refresh", default=1800, type=int, help="Refresh source playlist every N seconds")
    parser.add_argument("--probe-timeout", default=4.0, type=float, help="Seconds to wait while probing a source")
    parser.add_argument("--base-url", default=None, help="Public base URL to write into generated playlist")
    parser.add_argument("--once", default=None, help="Write a grouped proxy playlist to this file and exit")
    parser.add_argument(
        "--format",
        choices=("m3u8", "tvbox-txt"),
        default="m3u8",
        help="Output format used with --once",
    )
    args = parser.parse_args(argv)

    state = PlaylistState(args.source, refresh_seconds=args.refresh, probe_timeout=args.probe_timeout)
    state.refresh_if_needed(force=True)

    base_url = args.base_url
    if args.once:
        if not base_url:
            base_url = f"http://{guess_lan_ip()}:{args.port}"
        body = build_tvbox_txt(state.groups) if args.format == "tvbox-txt" else build_proxy_playlist(state.groups, base_url)
        with open(args.once, "w", encoding="utf-8") as handle:
            handle.write(body)
        print(f"Wrote {len(state.groups)} channels / {sum(len(c.sources) for c in state.groups.values())} sources to {args.once}")
        return 0

    handler = make_handler(state, fixed_base_url=base_url)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    lan_ip = guess_lan_ip()
    print(f"Loaded {len(state.groups)} channels / {sum(len(c.sources) for c in state.groups.values())} sources")
    print(f"Playlist: http://{lan_ip}:{args.port}/playlist.m3u8")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
