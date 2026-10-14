#!/usr/bin/env python3
"""Synthetic loopback evidence; no external services or operational data."""

from __future__ import annotations

import contextlib
import http.client
import json
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def dns_exchange():
    """Exchange one fixed DNS A query with a loopback-only fixture."""
    question = b"\x03lab\x04test\x00" + struct.pack("!HH", 1, 1)
    query = struct.pack("!6H", 42, 0x0100, 1, 0, 0, 0) + question
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.1", 0))
        server.settimeout(2)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(2)
            client.sendto(query, server.getsockname())
            received, peer = server.recvfrom(512)
            assert received == query
            answer = (struct.pack("!6H", 42, 0x8180, 1, 1, 0, 0) + question
                      + b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 30, 4)
                      + socket.inet_aton("127.0.0.1"))
            server.sendto(answer, peer)
            response, sender = client.recvfrom(512)
            assert sender == server.getsockname()
            assert response == answer
    return {"query": "lab.test A", "rcode": 0, "answers": 1,
            "address": "127.0.0.1", "ttl_seconds": 30,
            "query_bytes": len(query), "response_bytes": len(response)}


@contextlib.contextmanager
def serve(handler, tls=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    if tls:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(port, path, *, tls=None, timeout=2, key=None):
    connection = (http.client.HTTPSConnection("127.0.0.1", port,
                  context=tls, timeout=timeout) if tls else
                  http.client.HTTPConnection("127.0.0.1", port, timeout=timeout))
    try:
        connection.request("POST" if key else "GET", path,
                           headers={"Idempotency-Key": key} if key else {})
        response = connection.getresponse()
        body = response.read()
        return response.status, body
    finally:
        connection.close()


def run_lab():
    result = {"dns": dns_exchange()}
    with tempfile.TemporaryDirectory(prefix="systems-networking-lab-") as tmp:
        root = Path(tmp)
        events = root / "events.txt"
        events.write_text("", encoding="utf-8")
        events_lock = threading.Lock()


        def record_event(key):
            """Insert-or-ignore of one idempotency key in a file that outlives the server; True when new."""
            with events_lock:
                seen = set(events.read_text(encoding="utf-8").split())
                if key in seen:
                    return False
                with events.open("a", encoding="utf-8") as handle:
                    handle.write(key + "\n")
                return True

        calls = {"transient": 0, "unavailable": 0}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, status, body=b"synthetic-ok"):
                try:
                    self.send_response(status)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Expected when the timeout client has disconnected.

            def do_GET(self):
                if self.path == "/slow":
                    time.sleep(0.2)
                if self.path == "/transient":
                    calls["transient"] += 1
                    return self.reply(503 if calls["transient"] < 3 else 200)
                if self.path == "/unavailable":
                    calls["unavailable"] += 1
                    return self.reply(503)
                self.reply(200)

            def do_POST(self):
                if self.path != "/event" or not self.headers.get("Idempotency-Key"):
                    return self.reply(400)
                inserted = record_event(self.headers["Idempotency-Key"])
                # Simulate a committed operation whose acknowledgement was lost.
                if inserted:
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_WR)
                    return
                self.reply(200, b"already-applied")

        with serve(Handler) as port:
            assert request(port, "/health") == (200, b"synthetic-ok")
            result["http"] = {"status": 200, "transport": "loopback TCP"}
            try:
                request(port, "/slow", timeout=0.03)
                raise AssertionError("slow request did not time out")
            except TimeoutError:
                result["timeout"] = {"observed": True, "client_seconds": 0.03,
                                     "server_delay_seconds": 0.2}
            for endpoint in ("transient", "unavailable"):
                statuses = []
                backoffs = []
                for attempt in range(3):
                    status, _ = request(port, "/" + endpoint)
                    statuses.append(status)
                    if status == 200:
                        break
                    if attempt < 2:
                        delay = 0.01 * (2 ** attempt)
                        backoffs.append(delay)
                        time.sleep(delay)
                expected = [503, 503, 200] if endpoint == "transient" else [503] * 3
                assert statuses == expected
                assert calls[endpoint] == 3
                result[endpoint] = {"statuses": statuses, "attempts": 3,
                                    "backoff_seconds": backoffs,
                                    "outcome": "recovered" if statuses[-1] == 200 else "retry-budget-exhausted"}
            try:
                request(port, "/event", key="synthetic-event-1")
                raise AssertionError("commit acknowledgement was not lost")
            except http.client.RemoteDisconnected:
                pass

        # A new server over the same persisted keys replays the same event key.
        with serve(Handler) as port:
            assert request(port, "/event", key="synthetic-event-1") == (200, b"already-applied")
            assert request(port, "/event", key="synthetic-event-1") == (200, b"already-applied")
        assert len(events.read_text(encoding="utf-8").split()) == 1
        result["idempotency"] = {"attempts": 3, "effects": 1,
                                 "lost_acknowledgement": True, "server_recreated": True}

        cert, key = root / "cert.pem", root / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(key), "-out", str(cert), "-days", "1",
                        "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"],
                       check=True, capture_output=True, timeout=15)
        key.chmod(0o600)
        server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_tls.minimum_version = ssl.TLSVersion.TLSv1_3
        server_tls.load_cert_chain(cert, key)
        client_tls = ssl.create_default_context(cafile=str(cert))
        client_tls.minimum_version = ssl.TLSVersion.TLSv1_3
        with serve(Handler, server_tls) as port:
            assert request(port, "/health", tls=client_tls)[0] == 200
            with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
                with client_tls.wrap_socket(sock, server_hostname="localhost") as secured:
                    assert secured.version() == "TLSv1.3"
                    result["tls"] = {"version": secured.version(), "certificate_verified": True}
                    secured.sendall(b"GET /health HTTP/1.0\r\nHost: localhost\r\n\r\n")
                    with secured.makefile("rb") as response:
                        assert response.read().endswith(b"synthetic-ok")
            try:
                request(port, "/health", tls=ssl.create_default_context())
                raise AssertionError("untrusted certificate accepted")
            except ssl.SSLCertVerificationError:
                result["tls"]["untrusted_certificate_rejected"] = True
        result["status"] = "passed"
    return result


if __name__ == "__main__":
    print(json.dumps(run_lab(), indent=2))
