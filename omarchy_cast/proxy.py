"""A small TCP proxy used to enforce the receiver's IP allowlist.

PipeWire's ``module-native-protocol-tcp`` has no IP allowlist, so when the
receiver is in "specific" mode we bind that module to loopback only and run
this proxy on the public port. The proxy accepts a connection, checks the
peer's source IP against the allowlist, and only forwards allowed clients to
the loopback PipeWire listener. Unallowed clients are dropped before they ever
reach the audio server.

The allowlist can be replaced while the proxy is running (see
``set_allowed``), because the panel, the GTK window, and ``omarchy-cast
allow`` all edit the config of a receiver that is already up.
"""

import socket
import threading


def _shutdown(sock):
    """Wake whichever thread is blocked on this socket and send the FIN.

    Closing the fd would not do: the blocked ``recv``/``accept`` keeps the
    socket alive, so the peer would sit there until the thread gave up, and
    the fd number could meanwhile be handed to a new connection.
    """
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _close(sock):
    _shutdown(sock)
    try:
        sock.close()
    except OSError:
        pass


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
        self._lock = threading.Lock()
        self._allowed = set(allowed) if allowed is not None else None
        # Live connections, so a host that stops being allowed can be cut off
        # instead of keeping the session it already has.
        self._live = {}
        self._server = None
        self._accept_thread = None
        self._threads = []
        self._running = False

    @property
    def allowed(self):
        """The current allowlist as a set, or ``None`` when nothing is filtered."""
        with self._lock:
            return None if self._allowed is None else set(self._allowed)

    def set_allowed(self, allowed):
        """Replace the allowlist. Returns True when it actually changed.

        New connections are checked against the new list straight away;
        connections already open are left to ``drop_disallowed``.
        """
        new = set(allowed) if allowed is not None else None
        with self._lock:
            changed = new != self._allowed
            self._allowed = new
        return changed

    def drop_disallowed(self):
        """Close live connections whose source IP is no longer allowed.

        Returns the list of source IPs that were cut off. Without this, a host
        removed from the allowlist would keep the audio session it already
        had until the receiver stopped.
        """
        with self._lock:
            doomed = [
                (client, peer_ip, backend)
                for client, (peer_ip, backend) in self._live.items()
                if not self._allowed_locked(peer_ip)
            ]
        for client, _peer_ip, backend in doomed:
            # Shut the pair down and let the forwarding threads close the
            # sockets they own.
            _shutdown(client)
            _shutdown(backend)
        return [peer_ip for _client, peer_ip, _backend in doomed]

    def _allowed_locked(self, peer_ip):
        return self._allowed is None or peer_ip in self._allowed

    def _is_allowed(self, peer_ip):
        with self._lock:
            return self._allowed_locked(peer_ip)

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
        with self._lock:
            # Re-checked under the lock: the allowlist may have been replaced
            # while we were dialing the backend.
            still_allowed = self._allowed_locked(peer_ip)
            if still_allowed:
                self._live[client] = (peer_ip, backend)
        if not still_allowed:
            _close(client)
            _close(backend)
            return
        try:
            t1 = threading.Thread(target=self._forward, args=(client, backend), daemon=True)
            t2 = threading.Thread(target=self._forward, args=(backend, client), daemon=True)
            t1.start()
            t2.start()
            t1.join()
            t2.join()
        finally:
            with self._lock:
                self._live.pop(client, None)
            _close(client)
            _close(backend)

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
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(thread)

    def start(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.listen_host, self.listen_port))
        self._server.listen(16)
        self._running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def stop(self):
        self._running = False
        if self._server is not None:
            _shutdown(self._server)
            if self._accept_thread is not None:
                self._accept_thread.join(timeout=2)
                self._accept_thread = None
            # Closed only once the accept loop is out, so the port is free for
            # a receiver that rebinds it right away.
            _close(self._server)
            self._server = None
        with self._lock:
            live = list(self._live.items())
        for client, (_peer_ip, backend) in live:
            _shutdown(client)
            _shutdown(backend)
