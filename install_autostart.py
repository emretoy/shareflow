#!/usr/bin/env python3
"""ShareFlow - Açılışta otomatik başlatma kurulumu.

Mac: launchd plist oluşturur
Windows: Startup klasörüne shortcut ekler
"""

import os
import sys
import platform


def install_mac():
    """Mac'te launchd ile açılışta başlat."""
    shareflow_dir = os.path.dirname(os.path.abspath(__file__))
    python_path = sys.executable
    plist_path = os.path.expanduser(
        "~/Library/LaunchAgents/com.shareflow.server.plist"
    )

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.shareflow.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{shareflow_dir}/server.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{shareflow_dir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/shareflow-server.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/shareflow-server.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>
</dict>
</plist>
"""

    with open(plist_path, "w") as f:
        f.write(plist)

    print(f"[Mac] Plist oluşturuldu: {plist_path}")
    os.system(f"launchctl load {plist_path}")
    print("[Mac] ShareFlow server açılışta otomatik başlayacak")
    print(f"[Mac] Kaldırmak için: launchctl unload {plist_path}")


def install_windows():
    """Windows'ta Startup klasörüne bat dosyası ekle."""
    shareflow_dir = os.path.dirname(os.path.abspath(__file__))
    startup_dir = os.path.join(
        os.environ.get("APPDATA", ""),
        r"Microsoft\Windows\Start Menu\Programs\Startup",
    )

    bat_path = os.path.join(startup_dir, "shareflow-client.bat")
    bat = f"""@echo off
cd /d "{shareflow_dir}"
python client.py
"""

    with open(bat_path, "w") as f:
        f.write(bat)

    print(f"[Windows] Startup script oluşturuldu: {bat_path}")
    print("[Windows] ShareFlow client açılışta otomatik başlayacak")
    print(f"[Windows] Kaldırmak için silin: {bat_path}")


def uninstall_mac():
    plist_path = os.path.expanduser(
        "~/Library/LaunchAgents/com.shareflow.server.plist"
    )
    os.system(f"launchctl unload {plist_path} 2>/dev/null")
    if os.path.exists(plist_path):
        os.remove(plist_path)
        print("[Mac] Otomatik başlatma kaldırıldı")


def uninstall_windows():
    startup_dir = os.path.join(
        os.environ.get("APPDATA", ""),
        r"Microsoft\Windows\Start Menu\Programs\Startup",
    )
    bat_path = os.path.join(startup_dir, "shareflow-client.bat")
    if os.path.exists(bat_path):
        os.remove(bat_path)
        print("[Windows] Otomatik başlatma kaldırıldı")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "install"
    system = platform.system()

    if action == "uninstall":
        if system == "Darwin":
            uninstall_mac()
        else:
            uninstall_windows()
    else:
        if system == "Darwin":
            install_mac()
        else:
            install_windows()
