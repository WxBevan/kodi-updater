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

# Binary add-ons can have platform-specific versions. Presence is therefore
# verified, rather than comparing them with the version in this repository.
PRESENCE_ONLY_ADDONS = {
    "pvr.iptvsimple",
}

PROFILE_DIR = xbmcvfs.translatePath(
    f"special://profile/addon_data/{ADDON_ID}"
)
BUILD_VERSION_FILE = os.path.join(PROFILE_DIR, "build_version.txt")

# Kodi's repository refresh and automatic updater run asynchronously.
AUTO_UPDATE_TIMEOUT_SECONDS = 240
POLL_INTERVAL_SECONDS = 4
REPOSITORY_REFRESH_INTERVAL_SECONDS = 60


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
    """Return unique required add-ons and any exact versions in latest.json.

    Both formats remain supported:
      "addons": ["plugin.video.fenlight", ...]
      "addons": [{"id": "plugin.video.fenlight", "version": "2.1.69"}, ...]
    """
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
            unavailable.append(
                f"{addon_id} — no target version was found"
            )

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


def get_issues(targets):
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

    return missing, outdated


def format_issue_lines(missing, outdated, limit=20):
    lines = []

    for addon_id in missing:
        lines.append(f"{addon_id} — not installed")

    for addon_id, installed, target in outdated:
        lines.append(f"{addon_id} — {installed} → {target}")

    if len(lines) > limit:
        remaining = len(lines) - limit
        lines = lines[:limit] + [f"and {remaining} more"]

    return lines


def run_builtin(command, wait_ms=1000):
    log(f"Running builtin: {command}")
    xbmc.executebuiltin(command)
    xbmc.sleep(wait_ms)


def close_progress(dialog):
    try:
        dialog.close()
    except Exception:
        pass


def wait_for_automatic_updates(dialog, targets):
    """Wait for Kodi's normal repository updater to install available updates.

    This deliberately does not call InstallAddon(), because that built-in opens
    a separate confirmation dialog for every add-on. Missing add-ons should be
    resolved by the complete dependency list in addon.xml when Updater itself
    is installed or updated.
    """
    start = time.monotonic()
    next_refresh = REPOSITORY_REFRESH_INTERVAL_SECONDS

    while True:
        missing, outdated = get_issues(targets)
        if not missing and not outdated:
            return True, [], []

        elapsed = int(time.monotonic() - start)
        if elapsed >= AUTO_UPDATE_TIMEOUT_SECONDS:
            return False, missing, outdated

        if dialog.iscanceled():
            return None, missing, outdated

        remaining_count = len(missing) + len(outdated)
        percent = min(92, 15 + int((elapsed / AUTO_UPDATE_TIMEOUT_SECONDS) * 75))

        detail = ""
        if outdated:
            detail = outdated[0][0]
        elif missing:
            detail = missing[0]

        dialog.update(
            percent,
            f"Waiting for Kodi to install updates...\n"
            f"{remaining_count} remaining: {detail}",
        )

        if elapsed >= next_refresh:
            run_builtin("UpdateAddonRepos", 2000)
            next_refresh += REPOSITORY_REFRESH_INTERVAL_SECONDS

        xbmc.sleep(POLL_INTERVAL_SECONDS * 1000)


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
        required_entries = get_required_entries(latest)

        if not required_entries:
            close_progress(dialog)
            xbmcgui.Dialog().ok(
                "Updater",
                "No required add-ons were listed in latest.json.",
            )
            return

        dialog.update(5, "Refreshing repositories...")
        run_builtin("UpdateAddonRepos", 12000)

        dialog.update(10, "Reading required versions...")
        repo_versions = get_repo_versions()
        targets, unavailable = build_targets(latest, repo_versions)

        if unavailable:
            close_progress(dialog)
            xbmcgui.Dialog().ok(
                "Updater",
                "The update cannot continue because some required versions "
                "could not be found:\n\n"
                + "\n".join(unavailable[:20]),
            )
            return

        initial_missing, initial_outdated = get_issues(targets)

        if initial_missing or initial_outdated:
            success, missing, outdated = wait_for_automatic_updates(
                dialog,
                targets,
            )

            if success is None:
                close_progress(dialog)
                xbmcgui.Dialog().notification(
                    "Updater",
                    "Update cancelled",
                    xbmcgui.NOTIFICATION_WARNING,
                    4000,
                )
                return

            if not success:
                close_progress(dialog)
                issue_lines = format_issue_lines(missing, outdated)
                xbmcgui.Dialog().ok(
                    "Updater",
                    "Kodi did not finish all automatic add-on updates. "
                    "The build version was not saved.\n\n"
                    + "\n".join(issue_lines)
                    + "\n\nCheck that add-on updates are set to install "
                    "automatically and that 'Update official add-ons from' "
                    "is set to 'Any repositories', then run Update again.",
                )
                return

        dialog.update(94, "Refreshing installed add-ons...")
        run_builtin("UpdateLocalAddons", 4000)

        dialog.update(97, "Verifying update...")
        missing, outdated = get_issues(targets)

        if missing or outdated:
            close_progress(dialog)
            issue_lines = format_issue_lines(missing, outdated)
            xbmcgui.Dialog().ok(
                "Updater",
                "The final version check failed. The build version was not "
                "saved.\n\n"
                + "\n".join(issue_lines),
            )
            return

        write_local_build_version(latest_build)

        dialog.update(100, "Update complete.")
        xbmc.sleep(800)
        close_progress(dialog)

        changed = len(initial_missing) + len(initial_outdated)
        if changed:
            xbmcgui.Dialog().ok(
                "Updater",
                f"Update complete.\n\n{changed} required add-on(s) were "
                "installed or updated and every version was verified.",
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
