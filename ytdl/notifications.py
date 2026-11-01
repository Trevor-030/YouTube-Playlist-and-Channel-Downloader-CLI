"""Desktop notification when a long job finishes."""

import os
import shutil
import subprocess
import sys


def notify(title: str, body: str):
    """Best-effort desktop notification + sound. Never raises: a missing toast must not break a download."""
    env = {**os.environ, "YT_TITLE": title, "YT_BODY": body}     # env vars: no quoting problems with emoji or quotes
    try:
        if sys.platform == "win32":
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;"
                "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                "$x=$t.GetElementsByTagName('text');"
                "$x[0].AppendChild($t.CreateTextNode($env:YT_TITLE))|Out-Null;"
                "$x[1].AppendChild($t.CreateTextNode($env:YT_BODY))|Out-Null;"
                "$id='{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show([Windows.UI.Notifications.ToastNotification]::new($t))"
            )
            subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps], env=env,
                           capture_output=True, timeout=15, creationflags=0x08000000)   # no console flash
            import winsound
            winsound.MessageBeep()
        elif sys.platform == "darwin":
            subprocess.run(["osascript", "-e", "on run {t, b}", "-e", "display notification b with title t",
                            "-e", "end run", title, body], capture_output=True, timeout=15)
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", title, body], capture_output=True, timeout=15)
        else:
            print("\a", end="")
    except Exception:
        print("\a", end="")
