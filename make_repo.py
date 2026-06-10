import hashlib
import os
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
import stat
import time


ROOT_DIR = Path(__file__).resolve().parent
SOURCE_ADDONS_DIR = ROOT_DIR / "source_addons"
PUBLIC_DIR = ROOT_DIR / "docs"

EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    ".github",
    ".idea",
    ".vscode",
}

EXCLUDE_EXTS = {
    ".pyc",
    ".pyo",
    ".log",
    ".tmp",
    ".bak",
}


def remove_readonly(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        raise


def safe_rmtree(path):
    last_error = None

    for attempt in range(8):
        try:
            shutil.rmtree(path, onerror=remove_readonly)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.75)

    raise last_error


def clean_public_dir():
    PUBLIC_DIR.mkdir(exist_ok=True)

    for item in PUBLIC_DIR.iterdir():
        if item.name in {"latest.json", ".nojekyll"}:
            continue

        if item.is_dir():
            safe_rmtree(item)
        else:
            try:
                item.unlink()
            except PermissionError:
                os.chmod(item, stat.S_IWRITE)
                item.unlink()


def get_addon_info(addon_dir):
    addon_xml = addon_dir / "addon.xml"

    if not addon_xml.exists():
        raise FileNotFoundError(f"Missing addon.xml: {addon_dir}")

    # utf-8-sig handles files that start with a hidden BOM character.
    xml_text = addon_xml.read_text(encoding="utf-8-sig")
    root = ET.fromstring(xml_text)

    addon_id = root.attrib.get("id", "").strip()
    version = root.attrib.get("version", "").strip()

    if not addon_id:
        raise ValueError(f"Missing addon id in {addon_xml}")

    if not version:
        raise ValueError(f"Missing version in {addon_xml}")

    if addon_dir.name != addon_id:
        raise ValueError(
            f"Folder name must match addon id: folder={addon_dir.name}, addon id={addon_id}"
        )

    # This serialises only the <addon> node, without any <?xml ... ?> declaration.
    clean_addon_xml = ET.tostring(root, encoding="unicode")

    return addon_id, version, clean_addon_xml


def should_skip(path):
    parts = set(path.parts)

    if parts.intersection(EXCLUDE_DIRS):
        return True

    if path.suffix.lower() in EXCLUDE_EXTS:
        return True

    return False


def zip_addon(addon_dir, addon_id, version):
    out_dir = PUBLIC_DIR / addon_id
    out_dir.mkdir(parents=True, exist_ok=True)

    zip_path = out_dir / f"{addon_id}-{version}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in addon_dir.rglob("*"):
            if file_path.is_dir():
                continue

            rel_path = file_path.relative_to(addon_dir)

            if should_skip(rel_path):
                continue

            archive_name = Path(addon_id) / rel_path
            zf.write(file_path, archive_name.as_posix())

    return zip_path

def copy_asset_files(addon_dir, addon_id):
    out_dir = PUBLIC_DIR / addon_id

    asset_names = {
        "icon.png",
        "icon.jpg",
        "fanart.jpg",
        "fanart.png",
    }

    for asset_name in asset_names:
        source = addon_dir / asset_name
        if source.exists():
            dest = out_dir / asset_name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)

    resources_dir = addon_dir / "resources"

    if resources_dir.exists():
        for file_path in resources_dir.rglob("*"):
            if file_path.is_dir():
                continue

            if file_path.name.lower() in asset_names:
                rel_path = file_path.relative_to(addon_dir)
                dest = out_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file_path, dest)



def strip_xml_declaration(xml_text):
    xml_text = xml_text.lstrip("\ufeff").strip()
    return re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", xml_text, count=1).strip()

def build_addons_xml(addon_xml_texts):
    body = "\n\n".join(strip_xml_declaration(text) for text in addon_xml_texts)
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n{body}\n</addons>\n'


def write_md5(file_path):
    data = file_path.read_bytes()
    md5 = hashlib.md5(data).hexdigest()
    md5_path = file_path.with_suffix(file_path.suffix + ".md5")
    md5_path.write_text(md5, encoding="utf-8")
    return md5_path


def write_index(repo_zip_name=None):
    links = []

    if repo_zip_name:
        links.append(f'<p><a href="{repo_zip_name}">{repo_zip_name}</a></p>')

    links.append('<p><a href="addons.xml">addons.xml</a></p>')
    links.append('<p><a href="addons.xml.md5">addons.xml.md5</a></p>')

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Updater Repository</title>
</head>
<body>
    <h1>Updater Repository</h1>
    {"".join(links)}
</body>
</html>
"""
    (PUBLIC_DIR / "index.html").write_text(html, encoding="utf-8")


def ensure_latest_json():
    latest_file = PUBLIC_DIR / "latest.json"

    if not latest_file.exists():
        latest_file.write_text(
            '{\n  "build_version": "1.0.0",\n  "message": "A new update is available."\n}\n',
            encoding="utf-8"
        )


def main():
    if not SOURCE_ADDONS_DIR.exists():
        raise FileNotFoundError(f"Missing folder: {SOURCE_ADDONS_DIR}")

    clean_public_dir()
    ensure_latest_json()

    addon_xml_texts = []
    repo_root_zip_name = None

    addon_dirs = sorted(
        item for item in SOURCE_ADDONS_DIR.iterdir()
        if item.is_dir() and (item / "addon.xml").exists()
    )

    if not addon_dirs:
        raise RuntimeError("No add-on folders found in source_addons")

    for addon_dir in addon_dirs:
        addon_id, version, addon_xml_text = get_addon_info(addon_dir)

        print(f"Building {addon_id} {version}")

        zip_path = zip_addon(addon_dir, addon_id, version)
        copy_asset_files(addon_dir, addon_id)
        
        addon_xml_texts.append(addon_xml_text)

        # Put a copy of the repository zip at the web root so it is easy to install from Kodi.
        if addon_id == "repository.updater":
            repo_root_zip_name = zip_path.name
            shutil.copy2(zip_path, PUBLIC_DIR / zip_path.name)

    addons_xml = build_addons_xml(addon_xml_texts)

    addons_xml_path = PUBLIC_DIR / "addons.xml"
    addons_xml_path.write_text(addons_xml, encoding="utf-8")
    write_md5(addons_xml_path)

    write_index(repo_root_zip_name)

    print("")
    print("Repo built successfully.")
    print(f"Public files are in: {PUBLIC_DIR}")


if __name__ == "__main__":
    main()