from __future__ import annotations

import socket
import threading

import pytest

from jobpilot.portals.egress import EgressBlocked, EgressProxy, public_only, vet


def _echo_server() -> tuple[socket.socket, int]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()

    def serve() -> None:
        conn, _ = server.accept()
        with conn:
            data = conn.recv(1024)
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
                if data.startswith(b"GET")
                else b"echo:" + data
            )

    threading.Thread(target=serve, daemon=True).start()
    return server, server.getsockname()[1]


def _send(proxy: EgressProxy, payload: bytes) -> bytes:
    host, port = proxy.url.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=5) as s:
        s.sendall(payload)
        chunks = b""
        while chunk := s.recv(4096):
            chunks += chunk
            if (
                b"echo:" in chunks
                or chunks.endswith(b"ok")
                or b"Forbidden" in chunks
                and b"\r\n\r\n" in chunks
                and len(chunks) > 60
            ):
                break
        return chunks


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1"]
)
def test_policy_rejects_non_public(address: str) -> None:
    assert not public_only(address)


def test_vet_rejects_when_any_answer_is_private() -> None:
    with pytest.raises(EgressBlocked, match="non-public"):
        vet("rebind.example", 443, lambda h, p: ["93.184.215.14", "127.0.0.1"], public_only)


def test_vet_returns_the_address_it_checked() -> None:
    # The proxy connects to exactly this address, so a later DNS answer cannot swap it.
    assert vet("ok.example", 443, lambda h, p: ["93.184.215.14"], public_only) == "93.184.215.14"


def test_connect_to_loopback_is_refused() -> None:
    with EgressProxy() as proxy:
        reply = _send(proxy, b"CONNECT 127.0.0.1:8765 HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n\r\n")
    assert reply.startswith(b"HTTP/1.1 403")


def test_plain_http_to_metadata_is_refused() -> None:
    with EgressProxy() as proxy:
        reply = _send(
            proxy, b"GET http://169.254.169.254/latest/meta-data/ HTTP/1.1\r\nHost: x\r\n\r\n"
        )
    assert reply.startswith(b"HTTP/1.1 403")


def test_allowed_connect_tunnels_bytes() -> None:
    server, port = _echo_server()
    # The test server is on loopback, so allow it explicitly for this test only.
    with EgressProxy(resolver=lambda h, p: ["127.0.0.1"], allowed=lambda a: True) as proxy:
        host, pport = proxy.url.removeprefix("http://").split(":")
        with socket.create_connection((host, int(pport)), timeout=5) as s:
            s.sendall(f"CONNECT test.example:{port} HTTP/1.1\r\n\r\n".encode())
            assert s.recv(1024).startswith(b"HTTP/1.1 200")
            s.sendall(b"hello")
            assert s.recv(1024) == b"echo:hello"
    server.close()


def test_allowed_plain_http_is_rewritten_to_origin_form() -> None:
    server, port = _echo_server()
    with EgressProxy(resolver=lambda h, p: ["127.0.0.1"], allowed=lambda a: True) as proxy:
        reply = _send(
            proxy,
            f"GET http://test.example:{port}/jobs?x=1 HTTP/1.1\r\nHost: t\r\n\r\n".encode(),
        )
    assert reply.endswith(b"ok")
    server.close()


def test_plain_http_is_one_request_per_connection() -> None:
    """Regression: a reused keep-alive connection could carry a second request for another host
    (with that host's cookies) to the first, vetted upstream."""
    received: list[bytes] = []
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]

    def serve() -> None:
        conn, _ = server.accept()
        with conn:
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(4096)
            received.append(data)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")

    threading.Thread(target=serve, daemon=True).start()
    with EgressProxy(resolver=lambda h, p: ["127.0.0.1"], allowed=lambda a: True) as proxy:
        host, pport = proxy.url.removeprefix("http://").split(":")
        with socket.create_connection((host, int(pport)), timeout=5) as s:
            s.sendall(
                f"GET http://a.example:{port}/ HTTP/1.1\r\nHost: a.example\r\n"
                "Proxy-Connection: keep-alive\r\nConnection: keep-alive\r\n\r\n".encode()
            )
            reply = b""
            while chunk := s.recv(4096):
                reply += chunk
            # The proxy closed the client side after one response, so nothing else can ride it.
            assert reply.endswith(b"ok")
    server.close()
    [request] = received
    assert b"Connection: close" in request
    assert b"keep-alive" not in request.lower() and b"proxy-connection" not in request.lower()


def test_chunked_request_body_refused() -> None:
    with EgressProxy(resolver=lambda h, p: ["127.0.0.1"], allowed=lambda a: True) as proxy:
        reply = _send(
            proxy, b"POST http://x.example/ HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
        )
    assert reply.startswith(b"HTTP/1.1 400")
