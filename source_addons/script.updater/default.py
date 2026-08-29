import json
import os
import re
import sys
import time
import traceback
import urllib.request
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs
import shutil

ADDON_ID = "script.updater"
BUNDLE_ID = "script.flam.bundle"
TARGET_SKIN_ID = "skin.bingie"

LATEST_URL = "https://kodiplus.github.io/updater/latest.json"
ADDONS_XML_URL = "https://kodiplus.github.io/updater/addons.xml"

PRESENCE_ONLY_ADDONS = {
    "pvr.iptvsimple",
}

PROFILE_DIR = xbmcvfs.translatePath(
    f"special://profile/addon_data/{ADDON_ID}"
)
BUILD_VERSION_FILE = os.path.join(PROFILE_DIR, "build_version.txt")

OLD_REPO_SOURCE = "https://wxbevan.github.io/kodi-updater"
NEW_REPO_SOURCE = "https://kodiplus.github.io/updater"

SOURCES_XML = xbmcvfs.translatePath(
    "special://profile/sources.xml"
)

SOURCE_MIGRATION_MARKER = os.path.join(
    PROFILE_DIR,
    "kodiplus_source_migrated.txt",
)


INSTALL_TIMEOUT_SECONDS = 420
POLL_INTERVAL_SECONDS = 2
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

def migrate_file_manager_source():
    """
    Migrate the old WxBevan File Manager source to KodiPlus.

    This is intentionally non-fatal. The installed repository add-on
    already controls normal Kodi updates, so a File Manager migration
    failure must never fail an otherwise successful FLAM update.
    """
    temp_path = SOURCES_XML + ".kodiplus.tmp"

    try:
        ensure_profile_dir()

        # Migration has already completed on this Kodi profile.
        if os.path.exists(SOURCE_MIGRATION_MARKER):
            return True

        # A fresh Kodi installation may not have a sources.xml yet.
        if not os.path.exists(SOURCES_XML):
            with open(
                SOURCE_MIGRATION_MARKER,
                "w",
                encoding="utf-8",
            ) as marker:
                marker.write("not-needed")

            log(
                "File Manager source migration not needed: "
                "sources.xml does not exist."
            )
            return True

        with open(
            SOURCES_XML,
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as source_file:
            original = source_file.read()

        old_source_pattern = re.compile(
            re.escape(OLD_REPO_SOURCE),
            re.IGNORECASE,
        )

        # Fresh KodiPlus installs, or systems already migrated.
        if not old_source_pattern.search(original):
            with open(
                SOURCE_MIGRATION_MARKER,
                "w",
                encoding="utf-8",
            ) as marker:
                marker.write("not-needed")

            log(
                "Old File Manager repository source was not present."
            )
            return True

        updated = old_source_pattern.sub(
            NEW_REPO_SOURCE,
            original,
        )

        # Keep one safety backup of the original sources.xml.
        backup_path = SOURCES_XML + ".pre-kodiplus.bak"

        if not os.path.exists(backup_path):
            shutil.copy2(SOURCES_XML, backup_path)

        # Write to a temporary file first so sources.xml is never left
        # half-written if Kodi/device storage fails during the write.
        with open(
            temp_path,
            "w",
            encoding="utf-8",
            newline="",
        ) as destination_file:
            destination_file.write(updated)

        os.replace(temp_path, SOURCES_XML)

        with open(
            SOURCE_MIGRATION_MARKER,
            "w",
            encoding="utf-8",
        ) as marker:
            marker.write("migrated")

        log(
            "File Manager repository source migrated from "
            f"{OLD_REPO_SOURCE} to {NEW_REPO_SOURCE}."
        )
        return True

    except Exception as exc:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass

        log(
            f"Could not migrate File Manager repository source: {exc}",
            xbmc.LOGWARNING,
        )
        return False



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
    """Return the installed version without logging exceptions for missing add-ons."""
    try:
        if not xbmc.getCondVisibility(
            f"System.HasAddon({addon_id})"
        ):
            return None

        return (
            xbmcaddon.Addon(addon_id).getAddonInfo("version")
            or None
        )

    except Exception:
        return None


def json_rpc(method, params=None):
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
    }

    if params is not None:
        request["params"] = params

    try:
        response = xbmc.executeJSONRPC(json.dumps(request))
        return json.loads(response or "{}")

    except Exception as exc:
        log(
            f"JSON-RPC call failed for {method}: {exc}",
            xbmc.LOGWARNING,
        )
        return {}


def json_rpc_succeeded(response):
    if not isinstance(response, dict) or "error" in response:
        return False

    result = response.get("result")
    return result is not None and result is not False


def ensure_bingie_skin():
    try:
        if xbmc.getSkinDir() == TARGET_SKIN_ID:
            log("Bingie skin is already active.")
            return True

        if not get_installed_version(TARGET_SKIN_ID):
            log(
                "Bingie skin cannot be activated because it is not installed.",
                xbmc.LOGERROR,
            )
            return False

        enable_response = json_rpc(
            "Addons.SetAddonEnabled",
            {
                "addonid": TARGET_SKIN_ID,
                "enabled": True,
            },
        )

        if not json_rpc_succeeded(enable_response):
            log(
                "Could not explicitly enable Bingie: "
                f"{enable_response}",
                xbmc.LOGWARNING,
            )
        else:
            xbmc.sleep(500)

        response = json_rpc(
            "Settings.SetSettingValue",
            {
                "setting": "lookandfeel.skin",
                "value": TARGET_SKIN_ID,
            },
        )

        if not json_rpc_succeeded(response):
            log(
                "Kodi rejected the Bingie skin change: "
                f"{response}",
                xbmc.LOGERROR,
            )
            return False

        # Kodi normally changes immediately, but allow slower Fire TV
        # devices up to 20 seconds to unload Estuary and load Bingie.
        for _ in range(40):
            if xbmc.getSkinDir() == TARGET_SKIN_ID:
                log("Bingie skin activated successfully.")
                return True

            xbmc.sleep(500)

        log(
            "Kodi accepted the Bingie skin setting, but Bingie "
            "did not become active within 20 seconds.",
            xbmc.LOGERROR,
        )

    except Exception as exc:
        log(
            f"Could not activate Bingie skin: {exc}",
            xbmc.LOGERROR,
        )

    return False


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
    if dialog is None:
        return

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
            f"Installing FLAM build... "
            f"{remaining_count} remaining: {detail}",
        )

        xbmc.sleep(POLL_INTERVAL_SECONDS * 1000)


def install_or_update():
    first_install = any(
        argument.lower() == "first_install=true"
        for argument in sys.argv[1:]
    )

    title = "Installing FLAM" if first_install else "Updating FLAM"
    dialog = None

    xbmcgui.Dialog().notification(
        "Updater",
        f"{title}...",
        xbmcgui.NOTIFICATION_INFO,
        3000,
    )

    try:
        dialog = xbmcgui.DialogProgressBG()
        dialog.create("Updater", "Preparing FLAM build...")

        latest = get_latest_info()
        latest_build = str(latest.get("build_version", "0.0.0"))
        required_entries = get_required_entries(latest)

        if not required_entries:
            raise RuntimeError(
                "No required add-ons were listed in latest.json"
            )

        dialog.update(3, "Updater", "Refreshing repositories...")
        run_builtin("UpdateAddonRepos", 12000)

        dialog.update(6, "Updater", "Reading required versions...")
        repo_versions = get_repo_versions()
        targets, unavailable = build_targets(latest, repo_versions)

        if BUNDLE_ID not in targets:
            raise RuntimeError(
                f"{BUNDLE_ID} must be listed in latest.json "
                "with its version"
            )

        if unavailable:
            raise RuntimeError(
                "Required versions could not be found:\n"
                + "\n".join(unavailable[:20])
            )

        initial_missing, initial_outdated = get_issues(targets)

        if initial_missing or initial_outdated:
            dialog.update(
                8,
                "Updater",
                "Starting the FLAM bundle installation...",
            )

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

        dialog.update(
            96,
            "Updater",
            "Verifying the complete build...",
        )
        missing, outdated = get_issues(targets)

        if missing or outdated:
            issue_lines = format_issue_lines(missing, outdated)

            raise RuntimeError(
                "The final version check failed:\n\n"
                + "\n".join(issue_lines)
            )

        changed = len(initial_missing) + len(initial_outdated)

        if first_install:
            dialog.update(
                99,
                "Updater",
                "Activating Bingie skin...",
            )

            # Close the Estuary progress window before Kodi unloads it.
            close_progress(dialog)
            dialog = None

            if not ensure_bingie_skin():
                raise RuntimeError(
                    "Every required add-on was installed and verified, "
                    "but Kodi could not activate the Bingie skin. "
                    "The updater will try again on the next Kodi startup."
                )



            # Everything is installed and verified. Migrate any legacy
            # WxBevan File Manager source before completing the build.
            migrate_file_manager_source()

            # Do not mark the first installation complete until the skin
            # has also activated successfully.
            write_local_build_version(latest_build)

            xbmcgui.Dialog().notification(
                "Updater",
                "FLAM installation complete.",
                xbmcgui.NOTIFICATION_INFO,
                5000,
            )
            return

        # All required add-ons have now been installed and verified.
        migrate_file_manager_source()

        write_local_build_version(latest_build)

        dialog.update(
            100,
            "Updater",
            "FLAM build complete.",
        )

        xbmc.sleep(800)
        close_progress(dialog)
        dialog = None

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

        message = str(exc).strip() or type(exc).__name__
        log(
            f"{title} failed: {message}\n{traceback.format_exc()}",
            xbmc.LOGERROR,
        )

        xbmcgui.Dialog().ok(
            "FLAM installation failed"
            if first_install
            else "FLAM update failed",
            message,
        )


if __name__ == "__main__":
    install_or_update()
