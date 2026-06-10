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


def wait_until_installed(addon_id, timeout_seconds=45):
    waited = 0

    while waited < timeout_seconds:
        if is_addon_installed(addon_id):
            return True

        xbmc.sleep(1000)
        waited += 1

    return is_addon_installed(addon_id)


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
    dialog.create("Updater", "Preparing update...")

    try:
        latest = get_latest_info()
        latest_version = latest.get("build_version", "0.0.0")
        addons_to_install = get_required_addons(latest)

        if not addons_to_install:
            dialog.close()
            xbmcgui.Dialog().ok(
                "Updater",
                "No add-ons were listed in latest.json."
            )
            return

        dialog.update(5, "Refreshing repositories...")
        run_builtin("UpdateAddonRepos", 5000)

        failed = []
        total = len(addons_to_install)

        for index, addon_id in enumerate(addons_to_install, start=1):
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

            if not wait_until_installed(addon_id, 45):
                failed.append(addon_id)
                log(f"Failed to install {addon_id}", xbmc.LOGWARNING)
                continue

            run_builtin(f"EnableAddon({addon_id})", 500)

        dialog.update(90, "Refreshing installed add-ons...")
        run_builtin("UpdateLocalAddons", 4000)

        missing_after_update = [
            addon_id for addon_id in addons_to_install
            if not is_addon_installed(addon_id)
        ]

        failed = sorted(set(failed + missing_after_update))

        if failed:
            dialog.close()

            xbmcgui.Dialog().ok(
                "Updater",
                "Update did not fully complete.\n\n"
                "These add-ons are still missing:\n\n"
                + "\n".join(failed[:20])
            )

            # Do not save build_version if anything failed.
            # This means the popup will come back next startup.
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