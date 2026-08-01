import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs


ADDON_ID = "script.updater"
BUNDLE_ID = "script.flam.bundle"
LATEST_URL = "https://wxbevan.github.io/kodi-updater/latest.json"
ADDONS_XML_URL = "https://wxbevan.github.io/kodi-updater/addons.xml"

PRESENCE_ONLY_ADDONS = {
    "pvr.iptvsimple",
}

PROFILE_DIR = xbmcvfs.translatePath(
    f"special://profile/addon_data/{ADDON_ID}"
)
BUILD_VERSION_FILE = os.path.join(PROFILE_DIR, "build_version.txt")

INSTALL_TIMEOUT_SECONDS = 420
POLL_INTERVAL_SECONDS = 4
RETRY_AFTER_SECONDS = 210


def log(message, level=xbmc.LOGINFO):
    xbmc.log(f"[{ADDON_ID}] {message}", level)


def updater_version():
    try:
        return xbmcaddon.Addon(ADDON_ID).getAddonInfo("version") or "unknown"
    except Exception:
        return "unknown"


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
                    "User-Agent": f"Kodi-{ADDON_ID}/{updater_version()}",
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


def request_bundle_install():
    log(f"Requesting Kodi installation of {BUNDLE_ID}")
    xbmc.executebuiltin(f"InstallAddon({BUNDLE_ID})")


def wait_for_bundle(dialog, targets):
    start = time.monotonic()
    retried = False

    while True:
        missing, outdated = get_issues(targets)
        if not missing and not outdated:
            return True, [], []

        elapsed = int(time.monotonic() - start)
        if elapsed >= INSTALL_TIMEOUT_SECONDS:
            return False, missing, outdated

        if not retried and elapsed >= RETRY_AFTER_SECONDS:
            retried = True
            dialog.update(
                55,
                "Updater",
                "Refreshing repositories and retrying the FLAM bundle...",
            )
            run_builtin("UpdateAddonRepos", 10000)
            request_bundle_install()

        remaining_count = len(missing) + len(outdated)
        percent = min(
            92,
            10 + int((elapsed / INSTALL_TIMEOUT_SECONDS) * 82),
        )

        detail = outdated[0][0] if outdated else missing[0]
        dialog.update(
            percent,
            "Updater",
            f"Installing FLAM build... {remaining_count} remaining: {detail}",
        )

        xbmc.sleep(POLL_INTERVAL_SECONDS * 1000)


def install_or_update():
    first_install = any(
        argument.lower() == "first_install=true"
        for argument in sys.argv[1:]
    )

    title = "Installing FLAM" if first_install else "Updating FLAM"

    xbmcgui.Dialog().notification(
        "Updater",
        f"{title}...",
        xbmcgui.NOTIFICATION_INFO,
        3000,
    )

    dialog = xbmcgui.DialogProgressBG()
    dialog.create("Updater", "Preparing FLAM build...")

    try:
        latest = get_latest_info()
        latest_build = str(latest.get("build_version", "0.0.0"))
        required_entries = get_required_entries(latest)

        if not required_entries:
            raise RuntimeError("No required add-ons were listed in latest.json")

        dialog.update(3, "Updater", "Refreshing repositories...")
        run_builtin("UpdateAddonRepos", 12000)

        dialog.update(6, "Updater", "Reading required versions...")
        repo_versions = get_repo_versions()
        targets, unavailable = build_targets(latest, repo_versions)

        if BUNDLE_ID not in targets:
            raise RuntimeError(
                f"{BUNDLE_ID} must be listed in latest.json with its version"
            )

        if unavailable:
            raise RuntimeError(
                "Required versions could not be found:\n"
                + "\n".join(unavailable[:20])
            )

        initial_missing, initial_outdated = get_issues(targets)

        if initial_missing or initial_outdated:
            dialog.update(8, "Updater", "Starting the FLAM bundle installation...")
            request_bundle_install()

            success, missing, outdated = wait_for_bundle(
                dialog,
                targets,
            )

            if not success:
                issue_lines = format_issue_lines(missing, outdated)
                raise RuntimeError(
                    "Kodi did not finish the FLAM bundle installation.\n\n"
                    + "\n".join(issue_lines)
                )

        dialog.update(94, "Updater", "Refreshing installed add-ons...")
        run_builtin("UpdateLocalAddons", 4000)

        dialog.update(97, "Updater", "Verifying the complete build...")
        missing, outdated = get_issues(targets)

        if missing or outdated:
            issue_lines = format_issue_lines(missing, outdated)
            raise RuntimeError(
                "The final version check failed:\n\n"
                + "\n".join(issue_lines)
            )

        write_local_build_version(latest_build)

        dialog.update(100, "Updater", "FLAM build complete.")
        xbmc.sleep(800)
        close_progress(dialog)

        changed = len(initial_missing) + len(initial_outdated)
        if first_install:
            xbmcgui.Dialog().ok(
                "Updater",
                "FLAM installation complete. Every required add-on was "
                "installed and verified.",
            )
        elif changed:
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
        log(f"Install/update failed: {exc}", xbmc.LOGERROR)

        message = (
            "FLAM installation did not complete. Updater is still installed "
            "and will retry on the next Kodi startup.\n\n"
            if first_install
            else "The update did not complete. The build version was not saved.\n\n"
        )

        xbmcgui.Dialog().ok(
            "Updater",
            message + str(exc),
        )


if __name__ == "__main__":
    install_or_update()
