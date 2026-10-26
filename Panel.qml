import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Popup for the Audio Cast bar widget. It shells out to the `omarchy-cast`
// CLI: `list --json` to discover hosts, `connect`/`disconnect` to route audio,
// and `enable-receiver`/`disable-receiver` to toggle receiving on this host.
Panel {
  id: root
  moduleName: "yangkang5303.audio-cast"
  ipcTarget: "yangkang5303.audio-cast"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  property var hosts: []
  property bool receiverActive: false
  property bool busy: false
  property bool cliMissing: false
  property string status: ""

  property string allowMode: "specific"
  property var allowedIps: []
  property bool allowBusy: false
  // Set when the receiver that is running is an older build: it keeps the
  // allowlist it read at startup, so what is shown here is not what it
  // enforces until it is restarted.
  property bool receiverStale: false

  property string listOutput: ""

  readonly property color contentForeground: root.barForeground
  readonly property string contentFontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool anyConnected: {
    for (var i = 0; i < hosts.length; i++) if (hosts[i].connected) return true
    return false
  }

  function open() {
    refresh()
    root.controller.show()
  }

  function close() {
    root.controller.hide()
  }

  function toggle() {
    if (root.opened) root.close()
    else root.open()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function refresh() {
    root.busy = true
    root.status = "Scanning..."
    listProcess.running = true
    receiverProcess.running = true
    allowProcess.running = true
  }

  // Lightweight refresh that only re-reads the host list (including each
  // host's `connected` state) so the panel stays in sync with connection
  // changes made from the GTK window or the CLI.
  function syncConnections() {
    listProcess.running = true
  }

  function runAction(args, message) {
    root.busy = true
    root.status = message
    actionProcess.command = ["omarchy-cast"].concat(args)
    actionProcess.running = true
  }

  function connectTo(host) {
    runAction(["connect", host.name], "Connecting to " + host.name + "...")
  }

  function disconnectFrom(host) {
    runAction(["disconnect", host.name], "Disconnecting from " + host.name + "...")
  }

  function disconnectAll() {
    runAction(["disconnect", "--all"], "Disconnecting...")
  }

  function setReceiver(on) {
    root.receiverActive = on
    runAction([on ? "enable-receiver" : "disable-receiver"], on ? "Starting receiver..." : "Stopping receiver...")
  }

  function refreshAllow() {
    allowProcess.running = true
  }

  function setAllowMode(mode) {
    root.allowBusy = true
    allowActionProcess.command = ["omarchy-cast", "allow", "mode", mode]
    allowActionProcess.running = true
  }

  function isAllowed(ip) {
    for (var i = 0; i < root.allowedIps.length; i++)
      if (root.allowedIps[i] === ip) return true
    return false
  }

  function toggleAllow(host) {
    var ip = host.address
    root.allowBusy = true
    allowActionProcess.command = ["omarchy-cast", "allow", root.isAllowed(ip) ? "remove" : "add", ip]
    allowActionProcess.running = true
  }

  Process {
    id: listProcess
    command: ["omarchy-cast", "list", "--json", "--timeout", "5"]
    stdout: StdioCollector {
      id: listStdout
      waitForEnd: true
      onStreamFinished: root.listOutput = text
    }
    onExited: function(exitCode) {
      root.busy = false
      var out = String(root.listOutput || "")
      if (exitCode === 127) {
        root.cliMissing = true
        root.hosts = []
        root.status = "omarchy-cast is not installed"
        return
      }
      root.cliMissing = false
      var parsed = []
      try {
        var data = JSON.parse(out)
        if (Array.isArray(data)) parsed = data
      } catch (e) {
        parsed = []
      }
      root.hosts = parsed
      root.status = parsed.length > 0 ? (parsed.length + " host(s) found") : "No hosts found"
    }
  }

  Process {
    id: receiverProcess
    command: ["systemctl", "--user", "is-active", "omarchy-cast-receiver"]
    stdout: StdioCollector {
      id: receiverStdout
      waitForEnd: true
      onStreamFinished: root.receiverActive = (String(text).trim() === "active")
    }
  }

  Process {
    id: allowProcess
    command: ["omarchy-cast", "allow", "list", "--json"]
    stdout: StdioCollector {
      id: allowStdout
      waitForEnd: true
      onStreamFinished: function(text) {
        var data = {}
        try { data = JSON.parse(String(text)) } catch (e) { data = {} }
        root.allowMode = data.mode === "all" ? "all" : "specific"
        root.allowedIps = data.ips || []
        root.receiverStale = data.stale === true
      }
    }
  }

  Process {
    id: allowActionProcess
    command: []
    stdout: StdioCollector { waitForEnd: true }
    stderr: StdioCollector { waitForEnd: true }
    onExited: function(exitCode) {
      root.allowBusy = false
      Qt.callLater(root.refreshAllow)
    }
  }

  Process {
    id: actionProcess
    command: []
    stdout: StdioCollector { waitForEnd: true }
    stderr: StdioCollector { waitForEnd: true }
    onExited: function(exitCode) {
      root.busy = false
      Qt.callLater(root.refresh)
    }
  }

  // While the panel is open, periodically re-read the host list so the
  // connect/disconnect state stays in sync with the GTK window and CLI.
  Timer {
    id: syncTimer
    interval: 3000
    repeat: true
    running: root.opened
    onTriggered: root.syncConnections()
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTextKey: function(t) {
        if (t === "r" || t === "R") root.refresh()
      }

      Column {
        id: column
        width: parent.width
        spacing: Style.space(10)

        Item {
          width: parent.width
          height: Math.max(titleText.implicitHeight, scanButton.implicitHeight)

          Text {
            id: titleText
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: "Audio Cast"
            color: root.contentForeground
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.subtitle
            font.bold: true
          }

          PanelActionButton {
            id: scanButton
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            iconText: "󰍉"
            tooltipText: "Scan the network (r)"
            foreground: root.contentForeground
            fontFamily: root.contentFontFamily
            onClicked: root.refresh()
          }
        }

        Item {
          width: parent.width
          height: Math.max(receiverText.implicitHeight, receiverToggle.implicitHeight)

          Text {
            id: receiverText
            anchors.left: parent.left
            anchors.right: receiverToggle.left
            anchors.rightMargin: Style.space(12)
            anchors.verticalCenter: parent.verticalCenter
            text: "Receive audio on this host"
            color: root.contentForeground
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.body
          }

          ToggleSwitch {
            id: receiverToggle
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            checked: root.receiverActive
            busy: root.busy
            foreground: root.contentForeground
            onToggled: root.setReceiver(!root.receiverActive)
          }
        }

        Item {
          width: parent.width
          height: Math.max(allowText.implicitHeight, allowToggle.implicitHeight)

          Text {
            id: allowText
            anchors.left: parent.left
            anchors.right: allowToggle.left
            anchors.rightMargin: Style.space(12)
            anchors.verticalCenter: parent.verticalCenter
            text: "Restrict to allowed hosts"
            color: root.contentForeground
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.body
          }

          ToggleSwitch {
            id: allowToggle
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            checked: root.allowMode === "specific"
            busy: root.allowBusy
            foreground: root.contentForeground
            onToggled: root.setAllowMode(root.allowMode === "specific" ? "all" : "specific")
          }
        }

        // Say what the current allowlist state means for incoming audio: a
        // fresh install allows nobody until the user picks a host.
        Text {
          width: parent.width
          wrapMode: Text.WordWrap
          visible: text.length > 0
          text: root.allowMode === "specific"
            ? (root.allowedIps.length === 0
                ? "No host is allowed yet, so every incoming connection is refused. Use the allow button next to a host to let it cast here."
                : "")
            : "Any host that can reach this machine may cast audio to it."
          color: Qt.darker(root.contentForeground, 1.6)
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.bodySmall
        }

        // An older receiver is still running, so the list above is not yet
        // what it enforces. Restarting it is the only way to apply it.
        Text {
          width: parent.width
          wrapMode: Text.WordWrap
          visible: root.receiverStale
          text: "An older receiver is still running and keeps the allowlist it started with. Toggle receiving off and on to apply these settings."
          color: root.contentForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.bodySmall
        }

        Rectangle {
          width: parent.width
          height: 1
          color: root.contentForeground
          opacity: 0.12
        }

        PanelSectionHeader {
          text: "Remote hosts"
          foreground: root.contentForeground
          fontFamily: root.contentFontFamily
        }

        Column {
          width: parent.width
          spacing: Style.space(4)

          Repeater {
            model: root.hosts

            Item {
              required property var modelData
              width: column.width
              height: Math.max(hostColumn.implicitHeight, hostButton.implicitHeight) + Style.space(6)

              Column {
                id: hostColumn
                anchors.left: parent.left
                anchors.right: allowButton.left
                anchors.rightMargin: Style.space(12)
                anchors.verticalCenter: parent.verticalCenter
                spacing: 0

                Text {
                  text: modelData.name
                  color: root.contentForeground
                  font.family: root.contentFontFamily
                  font.pixelSize: Style.font.body
                }

                Text {
                  text: modelData.address + ":" + modelData.port
                  color: Qt.darker(root.contentForeground, 1.6)
                  font.family: root.contentFontFamily
                  font.pixelSize: Style.font.bodySmall
                }
              }

              PanelActionButton {
                id: allowButton
                anchors.right: hostButton.left
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                iconText: root.isAllowed(modelData.address) ? "" : ""
                tooltipText: root.isAllowed(modelData.address)
                  ? "Remove " + modelData.address + " from allowed hosts"
                  : "Add " + modelData.address + " to allowed hosts"
                foreground: root.isAllowed(modelData.address) ? Color.success : root.contentForeground
                fontFamily: root.contentFontFamily
                onClicked: root.toggleAllow(modelData)
              }

              PanelActionButton {
                id: hostButton
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                iconText: modelData.connected ? "󰅖" : "󰅂"
                tooltipText: modelData.connected ? "Disconnect" : "Connect"
                foreground: modelData.connected ? Color.urgent : root.contentForeground
                fontFamily: root.contentFontFamily
                onClicked: modelData.connected ? root.disconnectFrom(modelData) : root.connectTo(modelData)
              }
            }
          }
        }

        Text {
          visible: root.hosts.length === 0
          width: parent.width
          wrapMode: Text.WordWrap
          text: root.cliMissing
            ? "The omarchy-cast CLI is not installed. Install it from the project repository first."
            : "No hosts found. Enable the receiver on the other computer."
          color: Qt.darker(root.contentForeground, 1.6)
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.bodySmall
        }

        Item {
          visible: root.anyConnected
          width: parent.width
          height: disconnectAllButton.implicitHeight

          PanelActionButton {
            id: disconnectAllButton
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            iconText: "󰅖"
            tooltipText: "Disconnect all"
            foreground: Color.urgent
            fontFamily: root.contentFontFamily
            onClicked: root.disconnectAll()
          }

          Text {
            anchors.left: disconnectAllButton.right
            anchors.leftMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            text: "Disconnect all"
            color: root.contentForeground
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.bodySmall
          }
        }

        Text {
          width: parent.width
          text: root.status
          color: Qt.darker(root.contentForeground, 1.7)
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.caption
        }
      }
    }
  }
}
