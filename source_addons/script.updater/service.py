import json
import os
import re
import shutil
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

SESSION_PROPERTY = f"{ADDON_ID}.checked_this_session"

KEYMAP_SOURCE = (
    "special://home/addons/script.updater/resources/keymaps/stop_back.xml"
)
KEYMAP_DEST = (
    "special://profile/keymaps/kodi_updater_stop_back.xml"
)

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


def read_local_build_version():
    try:
        if not os.path.exists(BUILD_VERSION_FILE):
            return "0.0.0"

        with open(BUILD_VERSION_FILE, "r", encoding="utf-8") as file:
            return file.read().strip() or "0.0.0"
    except Exception as exc:
        log(f"Could not read local build version: {exc}", xbmc.LOGWARNING)
        return "0.0.0"


def write_local_build_version(version):
    ensure_profile_dir()

    with open(BUILD_VERSION_FILE, "w", encoding="utf-8") as file:
        file.write(str(version))


def cache_busted_url(url):
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}_={int(time.time())}"


def fetch_bytes(url, attempts=4, timeout=20):
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                cache_busted_url(url),
                headers={
                    "User-Agent": "Kodi-script.updater/1.0.8",
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


def get_required_entries(latest_info):
    entries = []
    seen = set()

    for entry in latest_info.get("addons", []):
        if isinstance(entry, dict):
            addon_id = str(entry.get("id", "")).strip()
            exact_version = str(entry.get("version", "")).strip() or None
        else:
            addon_id = str(entry).strip()
            exact_version = None

        if not addon_id or addon_id == ADDON_ID or addon_id in seen:
            continue

        seen.add(addon_id)
        entries.append((addon_id, exact_version))

    return entries


def build_targets(latest_info, repo_versions):
    targets = {}
    unavailable = []

    for addon_id, exact_version in get_required_entries(latest_info):
        if addon_id in PRESENCE_ONLY_ADDONS:
            targets[addon_id] = None
            continue

        target_version = exact_version or repo_versions.get(addon_id)
        if target_version:
            targets[addon_id] = target_version
        else:
            unavailable.append(addon_id)

    return targets, unavailable


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


def get_addon_issues(targets, unavailable):
    missing = []
    outdated = []

    for addon_id, target_version in targets.items():
        installed_version = get_installed_version(addon_id)

        if not installed_version:
            missing.append(addon_id)
            continue

        if addon_id in PRESENCE_ONLY_ADDONS:
            continue

        if not version_at_least(installed_version, target_version):
            outdated.append((addon_id, installed_version, target_version))

    return missing, outdated, list(unavailable)


def files_differ(source, destination):
    if not os.path.exists(destination):
        return True

    try:
        with open(source, "rb") as source_file:
            source_data = source_file.read()

        with open(destination, "rb") as destination_file:
            destination_data = destination_file.read()

        return source_data != destination_data
    except Exception:
        return True


def install_stop_back_keymap():
    try:
        source = xbmcvfs.translatePath(KEYMAP_SOURCE)
        destination = xbmcvfs.translatePath(KEYMAP_DEST)
        destination_dir = os.path.dirname(destination)

        if not os.path.exists(source):
            log(f"Stop-back keymap source missing: {source}", xbmc.LOGWARNING)
            return

        os.makedirs(destination_dir, exist_ok=True)

        if files_differ(source, destination):
            shutil.copy2(source, destination)
            log("Installed stop-back keymap.")
            xbmc.executebuiltin("Action(reloadkeymaps)")

    except Exception as exc:
        log(f"Failed to install stop-back keymap: {exc}", xbmc.LOGWARNING)


def stay_alive(monitor):
    while not monitor.abortRequested():
        if monitor.waitForAbort(60):
            break


def wait_for_first_install(targets, unavailable, monitor):
    # The service can start while Kodi is still resolving the dependency list
    # and switching to Bingie. Give that first installation time to settle.
    timeout_seconds = 240
    waited = 0

    while waited < timeout_seconds and not monitor.abortRequested():
        missing, outdated, unavailable_now = get_addon_issues(
            targets,
            unavailable,
        )

        if not missing and not outdated and not unavailable_now:
            return missing, outdated, unavailable_now

        if monitor.waitForAbort(10):
            break

        waited += 10

    return get_addon_issues(targets, unavailable)


def main():
    monitor = xbmc.Monitor()

    # Let Kodi finish startup, repository checks and dependency installation.
    if monitor.waitForAbort(30):
        return

    install_stop_back_keymap()

    window = xbmcgui.Window(10000)

    if window.getProperty(SESSION_PROPERTY) == "true":
        stay_alive(monitor)
        return

    try:
        latest = get_latest_info()
        repo_versions = get_repo_versions()

        latest_build = str(latest.get("build_version", "0.0.0"))
        message = latest.get("message", "A new update is available.")
        local_build = read_local_build_version()
        targets, unavailable = build_targets(latest, repo_versions)

        if local_build == "0.0.0":
            missing, outdated, unavailable = wait_for_first_install(
                targets,
                unavailable,
                monitor,
            )

            if monitor.abortRequested():
                return

            # A clean first install is the baseline, not an update.
            if not missing and not outdated and not unavailable:
                write_local_build_version(latest_build)
                window.setProperty(SESSION_PROPERTY, "true")
                log(
                    f"First install complete. Saved build version {latest_build}."
                )
                stay_alive(monitor)
                return
        else:
            missing, outdated, unavailable = get_addon_issues(
                targets,
                unavailable,
            )

        build_is_newer = version_tuple(latest_build) > version_tuple(local_build)
        addon_issues_exist = bool(missing or outdated or unavailable)

        window.setProperty(SESSION_PROPERTY, "true")

        if build_is_newer or addon_issues_exist:
            details = []

            if missing:
                details.append(f"Missing add-ons: {len(missing)}")

            if outdated:
                details.append(f"Add-on updates: {len(outdated)}")

            if unavailable:
                details.append(f"Versions unavailable: {len(unavailable)}")

            extra = ""
            if details:
                extra = "\n\n" + "\n".join(details)

            should_update = xbmcgui.Dialog().yesno(
                "A new update is available",
                f"{message}\n\n"
                f"Installed build: {local_build}\n"
                f"Available build: {latest_build}"
                f"{extra}",
                nolabel="Later",
                yeslabel="Update",
            )

            if should_update:
                xbmcgui.Dialog().notification(
                    "Updater",
                    "Starting update...",
                    xbmcgui.NOTIFICATION_INFO,
                    3000,
                )
                xbmc.executebuiltin(f"RunScript({ADDON_ID})")
            else:
                log("User selected Later.")

        else:
            log(
                f"No update needed. Local={local_build}, Latest={latest_build}"
            )

    except Exception as exc:
        # A restart will retry a failed network or repository check.
        log(f"Update check failed: {exc}", xbmc.LOGWARNING)

    stay_alive(monitor)


if __name__ == "__main__":
    main()
