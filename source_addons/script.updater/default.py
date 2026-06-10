import json
import os
import urllib.request

import xbmc
import xbmcgui
import xbmcvfs


ADDON_ID = "script.updater"
LATEST_URL = "https://wxbevan.github.io/kodi-updater/latest.json"

# Add every add-on you want the updater to install/update here.
# These IDs must exactly match each add-on's addon.xml id.
ADDONS_TO_INSTALL = [
    "script.updater",

    # Add your real add-ons below, for example:
    # "repository.cocoscrapers",
    # "plugin.video.fenlight",
    # "script.module.cocoscrapers",
    # "skin.bingie",
    # "script.bingie.helper",
    # "script.bingie.toolbox",
    # "script.bingie.widgets",
    # "script.skinshortcuts",
]


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


def run_builtin(command, wait_ms=1000):
    log(f"Running builtin: {command}")

    try:
        xbmc.executebuiltin(command)
    except TypeError:
        # Fallback for older Kodi builds.
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
    dialog.create("Updater", "Preparing update...")

    try:
        latest = get_latest_info()
        latest_version = latest.get("build_version", "0.0.0")

        dialog.update(5, "Refreshing repositories...")
        run_builtin("UpdateAddonRepos", 4000)

        total = len(ADDONS_TO_INSTALL)

        for index, addon_id in enumerate(ADDONS_TO_INSTALL, start=1):
            if dialog.iscanceled():
                dialog.close()

                xbmcgui.Dialog().notification(
                    "Updater",
                    "Update cancelled",
                    xbmcgui.NOTIFICATION_WARNING,
                    4000
                )
                return

            percent = 10 + int((index / max(total, 1)) * 75)

            dialog.update(
                percent,
                f"Installing/updating {addon_id}..."
            )

            run_builtin(f"InstallAddon({addon_id})", 2500)
            run_builtin(f"EnableAddon({addon_id})", 500)

        dialog.update(90, "Refreshing installed add-ons...")
        run_builtin("UpdateLocalAddons", 3000)

        # Only save the version after everything above has completed.
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
            "Update failed.\n\nPlease check your internet connection and try again.\n\n"
            f"{exc}"
        )


if __name__ == "__main__":
    install_or_update()