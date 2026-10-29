"""Per-editor bookkeeping for one Save All, so a failure cannot look like a success.

WHY THIS EXISTS (2026-10-29). `UnifiedMainWindow._on_save_all` saves a dozen
editors in sequence. Before this, each one set its own ad-hoc `saved_x` /
`x_save_ok` flag, every failure went only to `log_message(...)` — a side panel
the user need not have open — and the EVENTide dirty dot was cleared whether or
not its save had thrown. So a failed save was indistinguishable from a
successful one, and the "unsaved" marker vanished with the work still unsaved.

This is deliberately NOT a save architecture. It stores four states per editor
and answers three questions:

  * may this editor's dirty marker be cleared?   -> `may_clear_dirty(name)`
  * did anything fail?                            -> `any_failed()`
  * what do we tell the user?                     -> `summary_text()`

THE FOUR STATES ARE NOT INTERCHANGEABLE:

  nothing_to_save  the editor had no outstanding changes. Not a failure, and
                   its marker may be cleared because nothing is outstanding.
  saved            the change was persisted. Marker may be cleared.
  partial          some of it was persisted. Counts as a FAILURE for warning
                   purposes and the marker must STAY, so the user can retry
                   the rest.
  failed           nothing was persisted. Marker must STAY.

`all_ok()` means "nothing failed and nothing was partial" — it does NOT mean
"everything was written", because an editor with nothing to save also returns
True. Use `saved_names()` when you need to say what actually changed.
"""
from __future__ import annotations

NOTHING = "nothing"
SAVED = "saved"
PARTIAL = "partial"
FAILED = "failed"


class SaveOutcome:
    """Records what each editor did during one Save All."""

    def __init__(self) -> None:
        # name -> (state, detail); insertion-ordered so the summary reads in
        # the order the save actually happened.
        self._rows: dict[str, tuple[str, str]] = {}

    # ── recording ────────────────────────────────────────────────────────
    def nothing_to_save(self, name: str) -> None:
        self._rows[name] = (NOTHING, "")

    def saved(self, name: str, detail: str = "") -> None:
        self._rows[name] = (SAVED, detail)

    def partial(self, name: str, detail: str = "") -> None:
        self._rows[name] = (PARTIAL, detail)

    def failed(self, name: str, detail: str = "") -> None:
        self._rows[name] = (FAILED, detail)

    # ── querying ─────────────────────────────────────────────────────────
    def state(self, name: str) -> str | None:
        row = self._rows.get(name)
        return row[0] if row else None

    def may_clear_dirty(self, name: str) -> bool:
        """True only when the editor's changes are persisted, or there were
        none. An editor that was never recorded returns False — if we do not
        know what happened, we do not clear the user's marker."""
        return self.state(name) in (SAVED, NOTHING)

    def any_failed(self) -> bool:
        return any(s in (FAILED, PARTIAL) for s, _ in self._rows.values())

    def all_ok(self) -> bool:
        return not self.any_failed()

    def saved_names(self) -> list[str]:
        return [n for n, (s, _) in self._rows.items() if s == SAVED]

    def failed_names(self) -> list[str]:
        return [n for n, (s, _) in self._rows.items()
                if s in (FAILED, PARTIAL)]

    def nothing_names(self) -> list[str]:
        return [n for n, (s, _) in self._rows.items() if s == NOTHING]

    def should_warn(self) -> bool:
        """Warn only on a real failure. Having nothing to save is not one."""
        return self.any_failed()

    # ── reporting ────────────────────────────────────────────────────────
    def summary_text(self) -> str:
        """Plain-English summary. Names what saved AND what did not, and never
        claims everything saved when something did not."""
        ok = self.saved_names()
        bad = [(n, s, d) for n, (s, d) in self._rows.items()
               if s in (FAILED, PARTIAL)]
        lines: list[str] = []

        if bad:
            lines.append("These were NOT saved:" if len(bad) > 1
                         else "This was NOT saved:")
            for n, s, d in bad:
                if s == PARTIAL:
                    lines.append(f"  • {n} — only partly saved"
                                 + (f": {d}" if d else ""))
                else:
                    lines.append(f"  • {n}" + (f" — {d}" if d else ""))
            lines.append("")
            lines.append(
                ("Your unsaved changes in those are still there, so you can "
                 "fix the problem and save again.") if len(bad) > 1 else
                ("Your unsaved changes in it are still there, so you can fix "
                 "the problem and save again."))
            if ok:
                lines.append("")
                lines.append("Saved successfully: " + ", ".join(ok) + ".")
        elif ok:
            lines.append("Saved: " + ", ".join(ok) + ".")
        else:
            lines.append("There was nothing to save.")
        return "\n".join(lines)

    def log_lines(self) -> list[str]:
        """One line per editor, for the troubleshooting log."""
        order = {SAVED: "saved", NOTHING: "nothing to save",
                 PARTIAL: "PARTLY SAVED", FAILED: "FAILED"}
        out = []
        for n, (s, d) in self._rows.items():
            out.append(f"  {n}: {order.get(s, s)}" + (f" — {d}" if d else ""))
        return out

    def __len__(self) -> int:
        return len(self._rows)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SaveOutcome({self._rows!r})"
