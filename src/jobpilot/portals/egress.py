"""Local egress proxy that only lets the automated browser reach public internet addresses.

Chromium sends every connection (navigations, redirect hops, subresources, XHR, WebSockets,
service workers) through this proxy. For each connection the proxy resolves the host once,
refuses it if any resolved address is not public, and then connects to that exact vetted
address. Checking and connecting to the same address means DNS rebinding cannot swap in a
private IP between the check and the connection.
"""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import select
import socket
import socketserver
import threading
from collections.abc import Callable
from urllib.parse import urlsplit

from jobpilot.core.net import is_public_ip

log = logging.getLogger(__name__)

Resolver = Callable[[str, int], list[str]]
AddressPolicy = Callable[[str], bool]

MAX_HEADER_BYTES = 64 * 1024
CONNECT_TIMEOUT_S = 15
IDLE_TIMEOUT_S = 120


def system_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return list(dict.fromkeys(str(info[4][0]).split("%")[0] for info in infos))


def public_only(address: str) -> bool:
    return is_public_ip(ipaddress.ip_address(address))


class EgressBlocked(Exception):
    pass


def vet(host: str, port: int, resolver: Resolver, allowed: AddressPolicy) -> str:
    """Resolve once and return the address to connect to; raise if any answer is not allowed."""
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = resolver(host, port)
        except OSError as exc:
            raise EgressBlocked(f"cannot resolve {host}") from exc
    if not addresses:
        raise EgressBlocked(f"no addresses for {host}")
    if not all(allowed(a) for a in addresses):
        raise EgressBlocked(f"{host} resolves to a non-public address")
    return addresses[0]


class _Handler(socketserver.BaseRequestHandler):
    server: _ProxyServer

    def handle(self) -> None:
        client = self.request
        client.settimeout(IDLE_TIMEOUT_S)
        try:
            head = self._read_head(client)
            request_line, _, rest = head.partition(b"\r\n")
            method, target, version = request_line.decode("latin-1").split(" ", 2)
            if method.upper() == "CONNECT":
                host, _, port_text = target.rpartition(":")
                upstream = self._open(host.strip("[]"), int(port_text or 443))
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            else:
                parts = urlsplit(target)
                if parts.scheme not in ("http", "ws") or not parts.hostname:
                    raise EgressBlocked("only absolute http(s) requests are proxied")
                upstream = self._open(parts.hostname, parts.port or 80)
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                upstream.sendall(f"{method} {path} {version}\r\n".encode("latin-1") + rest)
        except EgressBlocked as exc:
            log.info("egress blocked: %s", exc)
            self._refuse(client, str(exc))
            return
        except (OSError, ValueError) as exc:
            self._refuse(client, f"bad request: {exc}", status=b"400 Bad Request")
            return
        self._pipe(client, upstream)

    def _read_head(self, client: socket.socket) -> bytes:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = client.recv(4096)
            if not chunk:
                raise ValueError("connection closed before headers")
            data += chunk
            if len(data) > MAX_HEADER_BYTES:
                raise ValueError("headers too large")
        return data

    def _open(self, host: str, port: int) -> socket.socket:
        address = vet(host, port, self.server.resolver, self.server.allowed)
        return socket.create_connection((address, port), timeout=CONNECT_TIMEOUT_S)

    @staticmethod
    def _refuse(client: socket.socket, reason: str, status: bytes = b"403 Forbidden") -> None:
        body = reason.encode()[:300]
        with contextlib.suppress(OSError):
            client.sendall(
                b"HTTP/1.1 " + status + b"\r\nContent-Type: text/plain\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n" + body
            )

    @staticmethod
    def _pipe(client: socket.socket, upstream: socket.socket) -> None:
        sockets = [client, upstream]
        try:
            while True:
                readable, _, broken = select.select(sockets, [], sockets, IDLE_TIMEOUT_S)
                if broken or not readable:
                    return
                for sock in readable:
                    data = sock.recv(65536)
                    if not data:
                        return
                    (upstream if sock is client else client).sendall(data)
        except OSError:
            return
        finally:
            upstream.close()


class _ProxyServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, resolver: Resolver, allowed: AddressPolicy) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.resolver = resolver
        self.allowed = allowed


class EgressProxy:
    """Context-managed proxy on 127.0.0.1 with an OS-assigned port."""

    def __init__(
        self, resolver: Resolver = system_resolver, allowed: AddressPolicy = public_only
    ) -> None:
        self._server = _ProxyServer(resolver, allowed)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="jobpilot-egress-proxy"
        )

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{str(host)}:{int(port)}"

    def start(self) -> EgressProxy:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> EgressProxy:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
