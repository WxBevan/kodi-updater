import json
import os
import urllib.request

import xbmc
import xbmcaddon
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


def ensure_profile_dir():
    if not os.path.exists(PROFILE_DIR):
        os.makedirs(PROFILE_DIR)


def write_local_build_version(version):
    ensure_profile_dir()
    with open(BUILD_VERSION_FILE, "w", encoding="utf-8") as file:
        file.write(str(version))


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
    return [addon_id for addon_id in addons if not is_addon_installed(addon_id)]


def run_builtin(command, wait_ms=1000):
    log(f"Running builtin: {command}")
    xbmc.executebuiltin(command)
    xbmc.sleep(wait_ms)


def install_or_update():
    xbmcgui.Dialog().notification(
        "Updater",
        "Update started",
        xbmcgui.NOTIFICATION_INFO,
        3000
    )

    dialog = xbmcgui.DialogProgress()
    dialog.create("Updater", "Checking setup...")

    try:
        latest = get_latest_info()
        latest_version = latest.get("build_version", "0.0.0")
        required_addons = get_required_addons(latest)

        dialog.update(15, "Refreshing repositories...")
        run_builtin("UpdateAddonRepos", 5000)

        dialog.update(35, "Refreshing installed add-ons...")
        run_builtin("UpdateLocalAddons", 5000)

        dialog.update(60, "Checking required add-ons...")
        xbmc.sleep(1500)

        missing = get_missing_addons(required_addons)

        if missing:
            dialog.close()

            xbmcgui.Dialog().ok(
                "Updater",
                "Some required add-ons are still missing.\n\n"
                "On a fresh install, install or update the Updater add-on from the repository again so Kodi can install its dependencies.\n\n"
                "Missing:\n"
                + "\n".join(missing[:20])
            )
            return

        write_local_build_version(latest_version)

        dialog.update(100, "Update complete.")
        xbmc.sleep(800)
        dialog.close()

        xbmcgui.Dialog().ok(
            "Updater",
            "Update complete."
        )

    except Exception as exc:
        try:
            dialog.close()
        except Exception:
            pass

        log(f"Update failed: {exc}", xbmc.LOGERROR)

        xbmcgui.Dialog().ok(
            "Updater",
            "Update failed.\n\n"
            "Please check your internet connection and try again.\n\n"
            f"{exc}"
        )


if __name__ == "__main__":
    install_or_update()