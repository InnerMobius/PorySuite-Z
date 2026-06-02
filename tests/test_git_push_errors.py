"""Tests for ``core/git_push_errors.classify_push_error``.

The classifier is pure (stdlib only, no I/O), so it's loaded directly
with ``importlib`` and tested against representative ``git push``
output strings copied from real failure modes.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_THIS_DIR, ".."))


def _load():
    path = os.path.join(_ROOT, "core", "git_push_errors.py")
    spec = importlib.util.spec_from_file_location(
        "git_push_errors", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("git_push_errors", module)
    spec.loader.exec_module(module)
    return module


# ─────────────────────────────────────────────────────────────── auth ──

@pytest.mark.parametrize("text", [
    # Token expired / not provided — typical https push without creds.
    (
        "remote: Support for password authentication was removed on August 13, 2021.\n"
        "remote: Please see https://docs.github.com/get-started/.../managing-your-personal-access-tokens for information on currently recommended modes of authentication.\n"
        "fatal: Authentication failed for 'https://github.com/foo/bar.git/'"
    ),
    # gh credential helper rejected the cached token.
    "remote: Invalid username or password.\nfatal: Authentication failed",
    # Bad creds returned by GitHub API.
    "remote: HTTP Basic: Access denied.\nfatal: Authentication failed for 'https://github.com/foo/bar.git/'",
    # Stored token revoked.
    "fatal: Bad credentials\nremote: Please see https://...",
    # SSH key not loaded.
    "Permission denied (publickey).\nfatal: Could not read from remote repository.",
    # Repo collaborator access revoked (still auth-flavoured).
    "remote: Permission to user/repo.git denied to attacker.\nfatal: unable to access",
    # Older git asking for username on stdin (no TTY).
    "fatal: could not read Username for 'https://github.com': No such device or address",
])
def test_classifies_auth_failures(text):
    mod = _load()
    assert mod.classify_push_error(text) == "auth"


# ──────────────────────────────────────────────── non-fast-forward ──

@pytest.mark.parametrize("text", [
    (
        "To https://github.com/foo/bar.git\n"
        " ! [rejected]        main -> main (non-fast-forward)\n"
        "error: failed to push some refs to 'https://github.com/foo/bar.git'\n"
        "hint: Updates were rejected because the tip of your current branch is behind\n"
        "hint: its remote counterpart. Integrate the remote changes (e.g.\n"
        "hint: 'git pull ...') before pushing again."
    ),
    (
        "To origin\n"
        " ! [rejected]        feature -> feature (fetch first)\n"
        "error: failed to push some refs to 'origin'"
    ),
])
def test_classifies_non_fast_forward(text):
    mod = _load()
    assert mod.classify_push_error(text) == "non_fast_forward"


# ───────────────────────────────────────────────────────── network ──

@pytest.mark.parametrize("text", [
    "fatal: unable to access 'https://github.com/foo/bar.git/': Could not resolve host: github.com",
    "fatal: unable to access 'https://github.com/foo/bar.git/': Failed to connect to github.com port 443: Connection refused",
    "fatal: unable to access 'https://github.com/foo/bar.git/': Operation timed out after 60000 milliseconds",
    "ssh: connect to host github.com port 22: Network is unreachable",
])
def test_classifies_network(text):
    mod = _load()
    assert mod.classify_push_error(text) == "network"


# ──────────────────────────────────────────────────────── no_repo ──

@pytest.mark.parametrize("text", [
    "remote: Repository not found.\nfatal: repository 'https://github.com/foo/bar.git/' not found",
    "ERROR: Repository not found.\nfatal: Could not read from remote repository.",
    # Local path typo (no .git there) — slightly different message.
    "fatal: '/path/to/nowhere' does not appear to be a git repository",
])
def test_classifies_no_repo(text):
    mod = _load()
    assert mod.classify_push_error(text) == "no_repo"


# ─────────────────────────────────────────────── protected_branch ──

def test_classifies_protected_branch():
    mod = _load()
    text = (
        "remote: error: GH006: Protected branch hook declined.\n"
        "remote: error: Required status check 'ci/build' is expected.\n"
        "To https://github.com/foo/bar.git\n"
        " ! [remote rejected] main -> main (protected branch hook declined)"
    )
    assert mod.classify_push_error(text) == "protected_branch"


# ───────────────────────────────────────────────────────────── misc ──

def test_unrecognised_returns_none():
    mod = _load()
    # Random unrelated git output should not match anything.
    assert mod.classify_push_error("Everything up-to-date") is None
    assert mod.classify_push_error("") is None
    assert mod.classify_push_error(None) is None


def test_priority_auth_before_network():
    # If both auth and network keywords appear, auth wins (more specific).
    mod = _load()
    text = (
        "fatal: Authentication failed\n"
        "(Also tried fallback but connection refused.)"
    )
    assert mod.classify_push_error(text) == "auth"


def test_case_insensitive():
    mod = _load()
    assert mod.classify_push_error("FATAL: AUTHENTICATION FAILED") == "auth"
    assert mod.classify_push_error("Could Not Resolve Host: example.com") == "network"
