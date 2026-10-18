import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME  # noqa: E402
from . import config, discovery, pulse, service, util  # noqa: E402


def run_async(func, on_done):
    def worker():
        try:
            result = func()
            error = None
        except Exception as exc:  # noqa: BLE001
            result, error = None, exc
        GLib.idle_add(on_done, result, error)

    threading.Thread(target=worker, daemon=True).start()


class CastWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(460, 560)
        self.cfg = config.load()
        self.hosts = []

        header = Gtk.HeaderBar()
        self.set_titlebar(header)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_tooltip_text("Scan the network")
        refresh.connect("clicked", lambda *_: self.refresh())
        header.pack_end(refresh)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for side in ("top", "bottom", "start", "end"):
            getattr(outer, f"set_margin_{side}")(16)
        self.set_child(outer)

        outer.append(self._build_this_host())
        outer.append(self._build_allowlist())
        outer.append(self._build_options())

        label = Gtk.Label(label="Remote hosts", xalign=0)
        label.add_css_class("heading")
        outer.append(label)

        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("boxed-list")
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(self.listbox)
        outer.append(scroller)

        self.status = Gtk.Label(label="Scanning...", xalign=0)
        self.status.add_css_class("dim-label")
        outer.append(self.status)

        # Keep connection state in sync with the Quickshell panel (or any
        # other client) by periodically re-reading the active tunnel sinks.
        GLib.timeout_add_seconds(3, self._sync_connections)

        self.refresh()

    def _build_this_host(self):
        frame = Gtk.Frame(label="This host")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(12)
        frame.set_child(box)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.append(Gtk.Label(label="Receive audio from other hosts", xalign=0, hexpand=True))
        self.receiver_switch = Gtk.Switch()
        self.receiver_switch.set_active(service.is_active())
        self.receiver_switch.set_valign(Gtk.Align.CENTER)
        if not service.UNIT_PATH.exists():
            self.receiver_switch.set_sensitive(False)
            self.receiver_switch.set_tooltip_text("Run 'omarchy-cast install' first")
        self.receiver_switch.connect("state-set", self._on_receiver_toggle)
        row.append(self.receiver_switch)
        box.append(row)

        name_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name_row.append(Gtk.Label(label="Display name", xalign=0))
        self.name_entry = Gtk.Entry(hexpand=True)
        self.name_entry.set_text(self.cfg["display_name"])
        name_row.append(self.name_entry)
        save = Gtk.Button(label="Save")
        save.connect("clicked", self._on_save_name)
        name_row.append(save)
        box.append(name_row)

        box.append(
            Gtk.Label(
                label=f"Advertised on port {self.cfg['port']}",
                xalign=0,
                css_classes=["dim-label"],
            )
        )
        return frame

    def _build_allowlist(self):
        frame = Gtk.Frame(label="Allowlist")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(12)
        frame.set_child(box)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.append(Gtk.Label(label="Restrict to allowed hosts", xalign=0, hexpand=True))
        self.allow_switch = Gtk.Switch()
        self.allow_switch.set_active(self.cfg.get("allow_mode", "all") == "specific")
        self.allow_switch.set_valign(Gtk.Align.CENTER)
        self.allow_switch.connect("state-set", self._on_allow_toggle)
        row.append(self.allow_switch)
        box.append(row)

        hint = Gtk.Label(
            label="When enabled, only hosts you add to the allowlist may cast audio to this host.",
            xalign=0,
            wrap=True,
            css_classes=["dim-label"],
        )
        box.append(hint)
        return frame

    def _is_allowed(self, ip):
        return ip in list(self.cfg.get("allowed_ips", []))

    def _on_allow_toggle(self, switch, state):
        mode = "specific" if state else "all"

        def do_set():
            config.set_allow_mode(mode)
            if service.is_active():
                service.stop()
                service.start()

        run_async(do_set, self._after_allow_change)
        self._set_status("Restricting to allowed hosts..." if state else "Allowing all hosts...")
        return False

    def _on_allow_toggle_host(self, _button, host):
        ip = host["address"]

        def do_toggle():
            if self._is_allowed(ip):
                config.allow_remove(ip)
            else:
                config.allow_add(ip)
            if service.is_active():
                service.stop()
                service.start()

        run_async(do_toggle, self._after_allow_change)
        self._set_status(f"Updating allowlist for {ip}...")

    def _after_allow_change(self, _result, error):
        if error:
            self._set_status(f"Error: {error}")
        else:
            self.cfg = config.load()
            self.allow_switch.set_active(self.cfg.get("allow_mode", "all") == "specific")
            self._populate(pulse.tunnel_sinks())
            self._set_status("Allowlist updated")
        return False

    def _build_options(self):
        frame = Gtk.Frame(label="Connection options")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(12)
        frame.set_child(box)
        self.opt_default = Gtk.CheckButton(label="Set as default output")
        self.opt_default.set_active(self.cfg.get("set_default", True))
        self.opt_move = Gtk.CheckButton(label="Move current playback")
        self.opt_move.set_active(self.cfg.get("move_streams", True))
        box.append(self.opt_default)
        box.append(self.opt_move)
        return frame

    def _on_save_name(self, _button):
        value = self.name_entry.get_text().strip()
        if not value:
            return
        self.cfg = config.set_value("display_name", value)
        if service.is_active():
            service.stop()
            service.start()
        self._set_status(f"Display name set to {value}")

    def _on_receiver_toggle(self, switch, state):
        def toggle():
            if state:
                service.enable(now=True)
            else:
                service.disable(now=True)

        run_async(toggle, self._after_toggle)
        with switch.handler_block_by_func(self._on_receiver_toggle):
            pass
        self._set_status("Starting receiver..." if state else "Stopping receiver...")
        return False

    def _after_toggle(self, _result, error):
        if error:
            self._set_status(f"Error: {error}")
        else:
            self._set_status("Receiver " + ("started" if service.is_active() else "stopped"))
        return False

    def _set_status(self, text):
        self.status.set_text(text)

    def refresh(self):
        self._set_status("Scanning...")
        run_async(self._discover, self._on_discovered)

    def _discover(self):
        cfg = config.load()
        hosts = discovery.discover()
        return hosts, pulse.tunnel_sinks(), cfg

    def _on_discovered(self, result, error):
        if error:
            self._set_status(f"Scan failed: {error}")
            return False
        self.hosts, connected, self.cfg = result
        self._populate(connected)
        if self.hosts:
            self._set_status(f"Found {len(self.hosts)} host(s)")
        else:
            self._set_status("No hosts found. Enable the receiver on the other machine.")
        return False

    def _clear(self, container):
        child = container.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            container.remove(child)
            child = nxt

    def _populate(self, connected):
        self._clear(self.listbox)
        if not self.hosts:
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            row.set_child(Gtk.Label(label="No hosts discovered", css_classes=["dim-label"]))
            self.listbox.append(row)
            return
        for host in self.hosts:
            sink = pulse.sink_name_for(host["name"])
            is_conn = sink in connected
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            for side in ("top", "bottom", "start", "end"):
                getattr(box, f"set_margin_{side}")(8)
            info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
            info.append(Gtk.Label(label=host["name"], xalign=0))
            info.append(
                Gtk.Label(
                    label=f"{host['address']}:{host['port']}",
                    xalign=0,
                    css_classes=["dim-label"],
                )
            )
            box.append(info)
            allowed = self._is_allowed(host["address"])
            allow_btn = Gtk.Button(label="Remove from allow" if allowed else "Add to allow")
            allow_btn.add_css_class("destructive-action" if allowed else "suggested-action")
            allow_btn.connect("clicked", self._on_allow_toggle_host, host)
            box.append(allow_btn)
            button = Gtk.Button(label="Disconnect" if is_conn else "Connect")
            button.add_css_class("suggested-action" if not is_conn else "destructive-action")
            button.connect("clicked", self._on_connect_clicked, host, is_conn)
            box.append(button)
            row.set_child(box)
            # Remember the host and connect button so _sync_connections can
            # update them in place when the connection state changes elsewhere.
            row._host = host
            row._button = button
            self.listbox.append(row)

    def _sync_connections(self):
        """Lightweight refresh of the connect/disconnect buttons so the GTK
        window reflects connection changes made from the Quickshell panel
        (or the CLI) without a full network re-scan."""
        connected = pulse.tunnel_sinks()
        row = self.listbox.get_first_child()
        while row is not None:
            host = getattr(row, "_host", None)
            button = getattr(row, "_button", None)
            if host is not None and button is not None:
                is_conn = pulse.sink_name_for(host["name"]) in connected
                button.set_label("Disconnect" if is_conn else "Connect")
                button.remove_css_class("suggested-action")
                button.remove_css_class("destructive-action")
                button.add_css_class("suggested-action" if not is_conn else "destructive-action")
            row = row.get_next_sibling()
        return True  # keep the timeout alive

    def _on_connect_clicked(self, _button, host, is_conn):
        if is_conn:
            self._set_status(f"Disconnecting from {host['name']}...")
            run_async(lambda: pulse.disconnect(host["name"]), self._after_action)
        else:
            self._set_status(f"Connecting to {host['name']}...")

            def do_connect():
                return pulse.connect(
                    host["address"],
                    host["port"],
                    host["name"],
                    set_default=self.opt_default.get_active(),
                    move_streams=self.opt_move.get_active(),
                )

            run_async(do_connect, self._after_action)

    def _after_action(self, _result, error):
        if error:
            self._set_status(f"Error: {error}")
        else:
            self._set_status("Done")
        self.refresh()
        return False


class CastApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        window = self.get_active_window()
        if not window:
            window = CastWindow(self)
        window.present()


def main():
    if not util.have("avahi-browse"):
        print("avahi-browse not found; install the avahi package")
    return CastApp().run([])
