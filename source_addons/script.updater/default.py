import json
import os
import urllib.request

import xbmc
import xbmcgui
import xbmcvfs


ADDON_ID = "script.updater"
LATEST_URL = "https://wxbevan.github.io/kodi-updater/latest.json"


def log(message, level=xbmc.LOGINFO):
    xbmc.log(f"[{ADDON_ID}] {message}", level)


def translate(path):
    return xbmcvfs.translatePath(path)


PROFILE_DIR = translate(f"special://profile/addon_data/{ADDON_ID}")
BUILD_VERSION_FILE = os.path.join(PROFILE_DIR, "build_version.txt")


def version_tuple(version):
    try:
        return tuple(int(part) for part in str(version).split("."))
    except Exception:
        return (0, 0, 0)


def read_local_build_version():
    try:
        if not os.path.exists(BUILD_VERSION_FILE):
            return "0.0.0"

        with open(BUILD_VERSION_FILE, "r", encoding="utf-8") as file:
            return file.read().strip() or "0.0.0"
    except Exception:
        return "0.0.0"


def get_latest_info():
    with urllib.request.urlopen(LATEST_URL, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    monitor = xbmc.Monitor()

    # Give Kodi time to finish loading the home screen.
    if monitor.waitForAbort(15):
        return

    try:
        latest = get_latest_info()

        latest_version = latest.get("build_version", "0.0.0")
        message = latest.get("message", "A new update is available.")
        local_version = read_local_build_version()

        if version_tuple(latest_version) <= version_tuple(local_version):
            log(f"No update needed. Local={local_version}, Latest={latest_version}")
            return

        dialog = xbmcgui.Dialog()

        should_update = dialog.yesno(
            "A new update is available",
            f"{message}\n\nInstalled version: {local_version}\nAvailable version: {latest_version}",
            nolabel="Later",
            yeslabel="Update"
        )

        if should_update:
            xbmc.executebuiltin(f"RunScript({ADDON_ID},mode=update)")
        else:
            # Do not save anything.
            # This means the popup will appear again next Kodi startup.
            log("User selected Later.")

    except Exception as exc:
        log(f"Update check failed: {exc}", xbmc.LOGWARNING)


if __name__ == "__main__":
    main()