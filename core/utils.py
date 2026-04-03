import os
import shutil
import re

def _camel(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", " ", name).strip()
    return "".join(part.capitalize() for part in base.split())

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def preprocess_c_file(path: str, project_info: dict | None = None) -> list[str]:
    """Return raw lines from the header without invoking external compilers.

    PorySuite runs purely on PyQt6 and should not depend on system toolchains.
    This helper intentionally avoids gcc/clang and simply returns the file
    contents. Callers that require light-weight include handling should manage
    it explicitly at parse-time.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return f.readlines()
    except OSError:
        return []


def clone_species_graphics(project_info: dict, source_species_const: str, target_species_const: str, display_name: str | None = None) -> list[str]:
    """Clone graphics folder for a species to a new species name.

    - Copies `graphics/pokemon/<source-slug>/**` to `graphics/pokemon/<target-slug>`.
    - Returns a list of created relative file paths.
    - Raises FileNotFoundError if the source folder is missing.
    """
    root = project_info.get("dir") or project_info.get("root") or os.getcwd()
    src_base = source_species_const[len("SPECIES_"):] if source_species_const.startswith("SPECIES_") else source_species_const
    dst_base = target_species_const[len("SPECIES_"):] if target_species_const.startswith("SPECIES_") else target_species_const
    src_slug = _slug(src_base)
    # Use display_name for target slug if provided to match user-visible casing
    dst_slug = _slug(display_name or dst_base)
    src_dir = os.path.join(root, "graphics", "pokemon", src_slug)
    dst_dir = os.path.join(root, "graphics", "pokemon", dst_slug)

    if not os.path.isdir(src_dir):
        raise FileNotFoundError(f"Missing source graphics folder: {os.path.abspath(src_dir)}")
    created: list[str] = []
    if os.path.isdir(dst_dir):
        # Already exists — nothing to copy
        return created
    os.makedirs(os.path.dirname(dst_dir), exist_ok=True)
    shutil.copytree(src_dir, dst_dir)
    for dirpath, _, filenames in os.walk(dst_dir):
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            created.append(rel.replace("\\", "/"))
    return created


def clone_item_graphics(project_info: dict, source_item_const: str, target_item_const: str, display_name: str | None = None) -> list[str]:
    """Clone item graphics from an existing item to a new item.

    FireRed item graphics are typically organized under `graphics/items/`.
    This clones the best-matching source folder/file(s) to the new item's slug.

    Returns a list of created relative file paths. No-op if destination exists.
    Raises FileNotFoundError if a suitable source cannot be found.
    """
    root = project_info.get("dir") or project_info.get("root") or os.getcwd()
    src_base = source_item_const[len("ITEM_"):] if source_item_const.startswith("ITEM_") else source_item_const
    dst_base = target_item_const[len("ITEM_"):] if target_item_const.startswith("ITEM_") else target_item_const
    src_slug = _slug(src_base)
    dst_slug = _slug(display_name or dst_base)

    items_root = os.path.join(root, "graphics", "items")
    if not os.path.isdir(items_root):
        raise FileNotFoundError(f"Missing items graphics directory: {os.path.abspath(items_root)}")

    # Prefer folder matches (graphics/items/<slug>) then single-file matches
    src_dir = os.path.join(items_root, src_slug)
    created: list[str] = []
    if os.path.isdir(src_dir):
        dst_dir = os.path.join(items_root, dst_slug)
        if os.path.isdir(dst_dir):
            return created
        os.makedirs(os.path.dirname(dst_dir), exist_ok=True)
        shutil.copytree(src_dir, dst_dir)
        for dirpath, _, filenames in os.walk(dst_dir):
            for fn in filenames:
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                created.append(rel.replace("\\", "/"))
        return created

    # Fallback: common single-file layout (e.g., icons under graphics/items)
    # Copy files whose basename starts with the source slug
    matched = []
    for dirpath, _, filenames in os.walk(items_root):
        for fn in filenames:
            base, ext = os.path.splitext(fn)
            if base == src_slug or base.startswith(src_slug + "_"):
                matched.append(os.path.join(dirpath, fn))
    if not matched:
        raise FileNotFoundError(f"Could not locate item graphics for {source_item_const}")
    os.makedirs(os.path.join(items_root), exist_ok=True)
    for path in matched:
        dirpath, fn = os.path.split(path)
        base, ext = os.path.splitext(fn)
        suffix = base[len(src_slug):]
        new_base = dst_slug + suffix
        dst_path = os.path.join(dirpath, new_base + ext)
        if os.path.exists(dst_path):
            continue
        shutil.copy2(path, dst_path)
        rel = os.path.relpath(dst_path, root)
        created.append(rel.replace("\\", "/"))
    return created
