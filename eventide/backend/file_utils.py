"""
Shared file utilities for EVENTide backend modules.

All path operations are relative to a project root directory, never CWD.
"""

import os
import re
from typing import List, Tuple, Callable, Optional


def is_text_file(path: str) -> bool:
    try:
        with open(path, 'rb') as f:
            chunk = f.read(4096)
        if b'\x00' in chunk:
            return False
        chunk.decode('utf-8')
        return True
    except Exception:
        return False


def replace_in_file(path: str, replacements: List[Tuple[str, str]],
                    whole_word: bool = False):
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        text = f.read()
    original = text
    for old, new in replacements:
        if whole_word:
            text = re.sub(r'\b%s\b' % re.escape(old), new.replace('\\', '\\\\'),
                          text)
        else:
            text = text.replace(old, new)
    if text != original:
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)


def replace_repo_wide(
    root_dir: str,
    replacements: List[Tuple[str, str]],
    callback: Optional[Callable[[str], None]] = None,
    whole_word: bool = False,
) -> None:
    """Replace text across the repository rooted at root_dir.

    `whole_word` matches only complete identifiers. Without it, renaming
    `LAYOUT_HOUSE3` also rewrites the middle of `LAYOUT_HOUSE30`, and any
    caller passing an empty replacement deletes every occurrence of a common
    word from every file in the project — which is how one delete turned
    `data/layouts/` into `data//` project-wide. Prefer it for anything
    identifier-shaped, and never pass an empty `new` here at all.
    """
    for root, dirs, files in os.walk(root_dir):
        if '.git' in dirs:
            dirs.remove('.git')
        for name in files:
            path = os.path.join(root, name)
            if not is_text_file(path):
                continue
            try:
                replace_in_file(path, replacements, whole_word=whole_word)
                if callback:
                    callback(path)
            except Exception:
                pass
