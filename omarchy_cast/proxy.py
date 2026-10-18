"""A small TCP proxy used to enforce the receiver's IP allowlist.

PipeWire's ``module-native-protocol-tcp`` has no IP allowlist, so when the
receiver is in "specific" mode we bind that module to loopback only and run
this proxy on the public port. The proxy accepts a connection, checks the
peer's source IP against the allowlist, and only forwards allowed clients to
the loopback PipeWire listener. Unallowed clients are dropped before they ever
reach the audio server.
"""

import socket
import threading


class TcpProxy:
    """Forward TCP connections to a backend, optionally filtering by source IP.

    ``allowed`` is an iterable of IP strings, or ``None`` to allow every
    client. The proxy listens on ``(listen_host, listen_port)`` and forwards
    to ``(backend_host, backend_port)``.
    """

    def __init__(self, listen_host, listen_port, backend_host, backend_port, allowed=None):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.allowed = set(allowed) if allowed is not None else None
        self._server = None
        self._threads = []
        self._running = False

    def _is_allowed(self, peer_ip):
        if self.allowed is None:
            return True
        return peer_ip in self.allowed

    def _forward(self, src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def _handle(self, client, addr):
        peer_ip = addr[0]
        if not self._is_allowed(peer_ip):
            client.close()
            return
        try:
            backend = socket.create_connection(
                (self.backend_host, self.backend_port), timeout=5
            )
        except OSError:
            client.close()
            return
        client.settimeout(30)
        backend.settimeout(30)
        t1 = threading.Thread(target=self._forward, args=(client, backend), daemon=True)
        t2 = threading.Thread(target=self._forward, args=(backend, client), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        try:
            client.close()
        except OSError:
            pass
        try:
            backend.close()
        except OSError:
            pass

    def _accept_loop(self):
        while self._running:
            try:
                client, addr = self._server.accept()
            except OSError:
                break
            thread = threading.Thread(
                target=self._handle, args=(client, addr), daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def start(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.listen_host, self.listen_port))
        self._server.listen(16)
        self._running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def stop(self):
        self._running = False
        try:
            self._server.close()
        except OSError:
            pass
