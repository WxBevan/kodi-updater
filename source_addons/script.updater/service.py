import json
import os
import urllib.request

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs


ADDON_ID = "script.updater"
LATEST_URL = "https://wxbevan.github.io/kodi-updater/latest.json"

SESSION_PROPERTY = f"{ADDON_ID}.checked_this_session"


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


def get_required_addons(latest_info):
    addons = latest_info.get("addons", [])
    return [str(addon_id).strip() for addon_id in addons if str(addon_id).strip()]


def is_addon_installed(addon_id):
    try:
        xbmcaddon.Addon(addon_id)
        return True
    except Exception:
        return False


def get_missing_addons(addons):
    return [
        addon_id for addon_id in addons
        if not is_addon_installed(addon_id)
    ]


def stay_alive(monitor):
    while not monitor.abortRequested():
        if monitor.waitForAbort(60):
            break


def main():
    monitor = xbmc.Monitor()

    if monitor.waitForAbort(15):
        return

    window = xbmcgui.Window(10000)

    if window.getProperty(SESSION_PROPERTY) == "true":
        stay_alive(monitor)
        return

    window.setProperty(SESSION_PROPERTY, "true")

    try:
        latest = get_latest_info()

        latest_version = latest.get("build_version", "0.0.0")
        message = latest.get("message", "A new update is available.")
        local_version = read_local_build_version()

        required_addons = get_required_addons(latest)
        missing_addons = get_missing_addons(required_addons)

        version_is_newer = version_tuple(latest_version) > version_tuple(local_version)
        required_addons_missing = len(missing_addons) > 0

        if version_is_newer or required_addons_missing:
            extra = ""

            if required_addons_missing:
                extra = (
                    "\n\nSome required add-ons are missing and need to be installed."
                    f"\nMissing: {len(missing_addons)}"
                )

            dialog = xbmcgui.Dialog()

            should_update = dialog.yesno(
                "A new update is available",
                f"{message}\n\n"
                f"Installed version: {local_version}\n"
                f"Available version: {latest_version}"
                f"{extra}",
                nolabel="Later",
                yeslabel="Update"
            )

            if should_update:
                xbmcgui.Dialog().notification(
                    "Updater",
                    "Starting update...",
                    xbmcgui.NOTIFICATION_INFO,
                    3000
                )
                xbmc.executebuiltin(f"RunScript({ADDON_ID},mode=update)")
            else:
                log("User selected Later.")

        else:
            log(f"No update needed. Local={local_version}, Latest={latest_version}")

    except Exception as exc:
        log(f"Update check failed: {exc}", xbmc.LOGWARNING)

    stay_alive(monitor)


if __name__ == "__main__":
    main()