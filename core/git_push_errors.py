"""Classify ``git push`` failure output into actionable categories.

Pure helper — no I/O, no Qt — so it can be tested in isolation.  Called
from the UI's push-done handler to pick which error dialog to show.

Why categorise at all
=====================

The raw ``git push`` output is plain enough for someone who's lived
inside git for years, but most ROM hackers haven't.  When the push
fails with ``fatal: Authentication failed`` they shouldn't have to know
that the fix is ``gh auth login`` — the editor should TELL them that.

Same goes for ``non-fast-forward`` (= ``git pull`` first), network
errors (= check your connection), and ``repository not found``
(= you typed the wrong remote URL or lost access).

Categories
==========

* ``"auth"`` — credentials expired / rejected / never given.  UI should
  offer to launch ``gh auth login``.
* ``"non_fast_forward"`` — local branch is behind remote.  UI should
  suggest ``git pull`` first.
* ``"network"`` — couldn't reach the remote.  UI should suggest
  checking the connection.
* ``"no_repo"`` — remote URL is wrong or no longer accessible.  UI
  should point at Configure Remote….
* ``"protected_branch"`` — remote refused the push because branch
  protection / required reviews block it.
* ``None`` — unrecognised failure; UI shows the raw git output.
"""

from __future__ import annotations

from typing import Optional


# Substrings inspected lower-case.  Each tuple is (category, substrings).
# Order matters: more specific categories first so a fall-through to the
# generic "auth" doesn't claim "non-fast-forward" pushes that happen to
# mention authentication in the trailing 'try git pull' hint text.
_PATTERNS = (
    # Auth — every flavour git emits when the credential layer rejects.
    ("auth", (
        "authentication failed",
        "could not read username",
        "could not read password",
        "401 unauthorized",
        "403 forbidden",
        "permission denied (publickey",
        "permission to ",  # "Permission to user/repo.git denied to ..."
        "support for password authentication was removed",
        "invalid username or password",
        "could not fetch credentials",
        "token expired",
        "bad credentials",
        "remote: invalid username",
        "fatal: authentication",
    )),
    # Branch protection — remote accepted the connection but blocked the push.
    # Checked BEFORE non_fast_forward because both messages include "rejected"
    # but the protected variant ALSO mentions the protection mechanism, and
    # that's what we want the dialog to mention (gh-pb / required reviewers).
    ("protected_branch", (
        "protected branch hook declined",
        "gh-pb",
        "required reviewers",
        "required status check",
    )),
    # Non-fast-forward — the most common "you need to pull first" case.
    # Patterns are scoped to the trailing parenthetical so a `[remote rejected]`
    # message (which the protected_branch check above already caught) doesn't
    # also match here.
    ("non_fast_forward", (
        "(non-fast-forward)",
        "(fetch first)",
        "updates were rejected because the remote contains work",
        "updates were rejected because the tip of your current branch is behind",
        "tip of your current branch is behind",
    )),
    # Network — couldn't reach the remote at all.
    ("network", (
        "could not resolve host",
        "could not connect",
        "connection timed out",
        "connection refused",
        "no route to host",
        "network is unreachable",
        "operation timed out",
        "ssl_error",
    )),
    # Repo not found / gone — typo'd URL or lost access.
    ("no_repo", (
        "remote: repository not found",
        "repository not found",
        "remote: not found",
        "does not appear to be a git repository",
    )),
)


def classify_push_error(output: str) -> Optional[str]:
    """Return the category name for a ``git push`` failure output, or None.

    ``output`` is the combined stdout+stderr from the push.  Match is
    case-insensitive substring against each pattern in
    ``_PATTERNS``; the first matching category wins.
    """
    if not output:
        return None
    text = output.lower()
    for category, needles in _PATTERNS:
        for needle in needles:
            if needle in text:
                return category
    return None
