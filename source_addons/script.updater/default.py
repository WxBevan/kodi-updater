import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs


ADDON_ID = "script.updater"
LATEST_URL = "https://wxbevan.github.io/kodi-updater/latest.json"
ADDONS_XML_URL = "https://wxbevan.github.io/kodi-updater/addons.xml"

# Binary add-ons can have different versions on Windows, Android and other
# platforms. For these, installation is verified by presence rather than by
# comparing against the version listed in this repository's addons.xml.
PRESENCE_ONLY_ADDONS = {
    "pvr.iptvsimple",
}

PROFILE_DIR = xbmcvfs.translatePath(
    f"special://profile/addon_data/{ADDON_ID}"
)
BUILD_VERSION_FILE = os.path.join(PROFILE_DIR, "build_version.txt")


def log(message, level=xbmc.LOGINFO):
    xbmc.log(f"[{ADDON_ID}] {message}", level)


def ensure_profile_dir():
    os.makedirs(PROFILE_DIR, exist_ok=True)


def write_local_build_version(version):
    ensure_profile_dir()
    with open(BUILD_VERSION_FILE, "w", encoding="utf-8") as file:
        file.write(str(version))


def cache_busted_url(url):
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}_={int(time.time())}"


def fetch_bytes(url, attempts=3, timeout=20):
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                cache_busted_url(url),
                headers={
                    "User-Agent": "Kodi-script.updater/1.0",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )

            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()

        except Exception as exc:
            last_error = exc
            log(
                f"Download attempt {attempt}/{attempts} failed for {url}: {exc}",
                xbmc.LOGWARNING,
            )

            if attempt < attempts:
                xbmc.sleep(3000)

    raise last_error


def get_latest_info():
    data = fetch_bytes(LATEST_URL)
    return json.loads(data.decode("utf-8-sig"))


def get_repo_versions():
    data = fetch_bytes(ADDONS_XML_URL)
    root = ET.fromstring(data)

    versions = {}

    for addon in root.findall("addon"):
        addon_id = addon.attrib.get("id", "").strip()
        version = addon.attrib.get("version", "").strip()

        if addon_id and version:
            versions[addon_id] = version

    return versions


def get_required_addons(latest_info):
    result = []

    for entry in latest_info.get("addons", []):
        if isinstance(entry, dict):
            addon_id = str(entry.get("id", "")).strip()
        else:
            addon_id = str(entry).strip()

        if addon_id and addon_id != ADDON_ID and addon_id not in result:
            result.append(addon_id)

    return result


def version_tuple(version):
    numbers = tuple(int(part) for part in re.findall(r"\d+", str(version)))
    return numbers or (0,)


def version_at_least(installed_version, target_version):
    installed = version_tuple(installed_version)
    target = version_tuple(target_version)
    length = max(len(installed), len(target))

    installed += (0,) * (length - len(installed))
    target += (0,) * (length - len(target))

    return installed >= target


def get_installed_version(addon_id):
    try:
        return xbmcaddon.Addon(addon_id).getAddonInfo("version") or None
    except Exception:
        return None


def addon_is_satisfied(addon_id, target_version):
    installed_version = get_installed_version(addon_id)

    if not installed_version:
        return False

    if addon_id in PRESENCE_ONLY_ADDONS:
        return True

    return version_at_least(installed_version, target_version)


def build_update_plan(required_addons, repo_versions):
    updates = []
    unavailable = []

    for addon_id in required_addons:
        target_version = repo_versions.get(addon_id)
        installed_version = get_installed_version(addon_id)

        if not target_version:
            if addon_id in PRESENCE_ONLY_ADDONS and installed_version:
                continue

            unavailable.append(
                f"{addon_id} — not found in repository metadata"
            )
            continue

        if not addon_is_satisfied(addon_id, target_version):
            updates.append((addon_id, target_version, installed_version))

    return updates, unavailable


def run_builtin(command, wait_ms=1000):
    log(f"Running builtin: {command}")
    xbmc.executebuiltin(command)
    xbmc.sleep(wait_ms)


def wait_for_target(addon_id, target_version, timeout_seconds=120):
    waited = 0

    while waited < timeout_seconds:
        installed_version = get_installed_version(addon_id)

        if installed_version:
            if addon_id in PRESENCE_ONLY_ADDONS:
                return True

            if version_at_least(installed_version, target_version):
                return True

        xbmc.sleep(2000)
        waited += 2

    return False


def install_addon(addon_id, target_version):
    # First attempt.
    run_builtin(f"InstallAddon({addon_id})", 3000)

    if wait_for_target(addon_id, target_version, 90):
        return True

    # Refresh once more and retry. This helps when Kodi had stale repository
    # metadata or the first download was interrupted.
    log(f"Retrying installation of {addon_id}", xbmc.LOGWARNING)
    run_builtin("UpdateAddonRepos", 10000)
    run_builtin(f"InstallAddon({addon_id})", 3000)

    return wait_for_target(addon_id, target_version, 120)


def final_verification(required_addons, repo_versions):
    failures = []

    for addon_id in required_addons:
        target_version = repo_versions.get(addon_id)
        installed_version = get_installed_version(addon_id)

        if addon_id in PRESENCE_ONLY_ADDONS:
            if not installed_version:
                failures.append(f"{addon_id} — not installed")
            continue

        if not target_version:
            failures.append(f"{addon_id} — not found in repository metadata")
            continue

        if not installed_version:
            failures.append(
                f"{addon_id} — required {target_version}, installed none"
            )
            continue

        if not version_at_least(installed_version, target_version):
            failures.append(
                f"{addon_id} — required {target_version}, "
                f"installed {installed_version}"
            )

    return failures


def close_progress(dialog):
    try:
        dialog.close()
    except Exception:
        pass


def install_or_update():
    xbmcgui.Dialog().notification(
        "Updater",
        "Update started",
        xbmcgui.NOTIFICATION_INFO,
        3000,
    )

    dialog = xbmcgui.DialogProgress()
    dialog.create("Updater", "Preparing update...")

    try:
        latest = get_latest_info()
        latest_build = str(latest.get("build_version", "0.0.0"))
        required_addons = get_required_addons(latest)

        if not required_addons:
            close_progress(dialog)
            xbmcgui.Dialog().ok(
                "Updater",
                "No required add-ons were listed in latest.json.",
            )
            return

        dialog.update(5, "Refreshing repositories...")
        run_builtin("UpdateAddonRepos", 10000)

        dialog.update(12, "Reading repository versions...")
        repo_versions = get_repo_versions()

        updates, unavailable = build_update_plan(
            required_addons,
            repo_versions,
        )

        if unavailable:
            close_progress(dialog)
            xbmcgui.Dialog().ok(
                "Updater",
                "The update cannot continue because some add-ons were not "
                "found in the repository:\n\n"
                + "\n".join(unavailable[:20]),
            )
            return

        failed = []
        total = len(updates)

        for index, (addon_id, target_version, installed_version) in enumerate(
            updates,
            start=1,
        ):
            if dialog.iscanceled():
                close_progress(dialog)
                xbmcgui.Dialog().notification(
                    "Updater",
                    "Update cancelled",
                    xbmcgui.NOTIFICATION_WARNING,
                    4000,
                )
                return

            percent = 15 + int((index / max(total, 1)) * 70)
            current_text = installed_version or "not installed"

            dialog.update(
                percent,
                f"Updating {addon_id}\n{current_text} → {target_version}",
            )

            if not install_addon(addon_id, target_version):
                actual_version = get_installed_version(addon_id) or "none"
                failed.append(
                    f"{addon_id} — required {target_version}, "
                    f"installed {actual_version}"
                )
                log(f"Failed to update {addon_id}", xbmc.LOGERROR)

        dialog.update(90, "Refreshing installed add-ons...")
        run_builtin("UpdateLocalAddons", 5000)

        dialog.update(95, "Verifying update...")
        verification_failures = final_verification(
            required_addons,
            repo_versions,
        )

        failed = sorted(set(failed + verification_failures))

        if failed:
            close_progress(dialog)
            xbmcgui.Dialog().ok(
                "Updater",
                "The update did not fully complete. The build version was "
                "not saved.\n\n"
                + "\n".join(failed[:20])
                + "\n\nRestart Kodi and run Update again.",
            )
            return

        # The build is recorded only after every required add-on has passed
        # final version verification.
        write_local_build_version(latest_build)

        dialog.update(100, "Update complete.")
        xbmc.sleep(800)
        close_progress(dialog)

        if updates:
            updated_names = ", ".join(item[0] for item in updates[:6])
            if len(updates) > 6:
                updated_names += f" and {len(updates) - 6} more"

            xbmcgui.Dialog().ok(
                "Updater",
                f"Update complete.\n\nUpdated: {updated_names}",
            )
        else:
            xbmcgui.Dialog().ok(
                "Updater",
                "Update complete. All required add-ons were already current.",
            )

    except Exception as exc:
        close_progress(dialog)
        log(f"Update failed: {exc}", xbmc.LOGERROR)

        xbmcgui.Dialog().ok(
            "Updater",
            "Update failed. The build version was not saved.\n\n"
            "Please check the internet connection and try again.\n\n"
            f"{exc}",
        )


if __name__ == "__main__":
    install_or_update()
