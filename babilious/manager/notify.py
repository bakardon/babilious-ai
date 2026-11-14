"""Windows desktop notifications (toast), with no extra packages.

Uses the WinRT toast API through Windows PowerShell under PowerShell's own app
identity, so notifications appear in the Action Center even when the
Babilious tab is closed. Failures are logged, never raised: a missed toast must
not break the reminder itself (it is still shown in the app).
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from xml.sax.saxutils import escape

from .. import db

log = logging.getLogger(__name__)
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

SCRIPT = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@'
<toast scenario="reminder"><visual><binding template="ToastGeneric"><text>{title}</text><text>{body}</text></binding></visual>
<actions><action content="OK" arguments="dismiss" activationType="system"/></actions>
<audio src="ms-winsoundevent:Notification.Reminder"/></toast>
'@)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app}').Show($toast)
"""


def enabled() -> bool:
    return os.name == "nt" and bool(db.get_setting("desktop_notifications", True))


def toast(title: str, body: str = "", wait: bool = False) -> bool:
    """Show a Windows notification. Returns True if PowerShell accepted it (when wait=True)."""
    if not enabled():
        return False
    script = (SCRIPT.replace("{title}", escape(title[:120])).replace("{body}", escape(body[:300]))
              .replace("{app}", APP_ID))

    def run() -> bool:
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                               capture_output=True, timeout=20,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0:
                log.warning("Toast failed: %s", r.stderr.decode("utf-8", "replace")[:300])
            return r.returncode == 0
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("Toast failed: %s", exc)
            return False
    if wait:
        return run()
    threading.Thread(target=run, daemon=True, name="toast").start()
    return True
