#!/usr/bin/env python3
# =============================================================================
# bridge.py — the intelligent middleman between Bulwark and ScriptVault
# -----------------------------------------------------------------------------
# PURPOSE
#   Bulwark and ScriptVault are two SEPARATE tools that both care about the
#   scripts scattered around your machine:
#
#     • Bulwark    — a read-only Rust CLI that *classifies* scripts (risk level,
#                    owner, category, language, description). It prints JSON with
#                    `bulwark scan --json`.
#     • ScriptVault — a Rust TUI that *browses & searches* scripts. For each
#                    script it can read a "sidecar" file named
#                    `<script>.scriptvault.yaml` to get display metadata
#                    (name, desc, tags, …).
#
#   This bridge translates Bulwark's classification INTO ScriptVault's sidecar
#   format, so risk/owner badges show up inside ScriptVault's search results and
#   preview pane — WITHOUT merging the two tools.
#
# HOW IT STAYS SEPARATE (the whole point)
#   This script imports nothing from either project. It talks to Bulwark only
#   through its command-line interface (a subprocess), and talks to ScriptVault
#   only by writing the YAML sidecar files ScriptVault already knows how to read.
#   The only thing shared is a *data format*. Either tool can change its guts
#   freely as long as the JSON shape and the sidecar format stay put.
#
# SAFETY MODEL
#   Sidecars are written next to your scripts (in ~/bin, dotfiles, etc.), so
#   writing is opt-in: by default we DRY-RUN (show what we *would* write). You
#   must pass --apply to actually touch disk. We are also non-clobbering: we read
#   any existing sidecar, merge, and only manage our OWN `risk:`/`owner:` tags and
#   the `[RISK: …]` desc badge. Your hand-written fields are never destroyed.
#
# USAGE
#   bridge.py                          # report mode: print a joined risk+name table
#   bridge.py --write-sidecars         # DRY-RUN: show the sidecars we'd write
#   bridge.py --write-sidecars --apply # actually write the sidecars
#   bridge.py ~/bin ~/.local/bin       # restrict the scan to these paths
#   bridge.py --bulwark /path/to/bulwark   # point at a specific bulwark binary
# =============================================================================

# `from __future__ import annotations` makes all type hints lazy (stored as
# strings, not evaluated at runtime). It lets us write modern hints like
# `list[dict]` even on slightly older 3.x, and avoids import-order headaches.
from __future__ import annotations

# --- Standard library only (no third-party except PyYAML) --------------------
import argparse          # builds the --flag command-line interface for us
import json              # parses Bulwark's JSON output
import re                # strips our old [RISK: …] badge for clean re-application
import shutil            # shutil.which() finds an executable on the PATH
import subprocess        # runs the `bulwark` binary as a child process
import sys               # stderr printing and exit codes
from pathlib import Path # OS-aware paths — see Learning Notes for why not str

# PyYAML is the one external dependency. We import it lazily-ish at top level but
# guard with a friendly message, because a missing dep is the single most likely
# "why won't it run" for a new user.
try:
    import yaml
except ImportError:  # pragma: no cover - environment-dependent
    sys.exit(
        "bridge.py needs PyYAML. Install it with:  pip install pyyaml\n"
        "(PyYAML is how we read & write ScriptVault's .scriptvault.yaml sidecars.)"
    )

# =============================================================================
# CONSTANTS — the two halves of the data contract, named in one place.
# -----------------------------------------------------------------------------
# Keeping these as named constants (instead of sprinkling string literals
# through the code) means: if ScriptVault renames its sidecar suffix, or we want
# a different tag prefix, there is exactly ONE line to change.
# =============================================================================

# ScriptVault looks for `<full-filename>.scriptvault.yaml` (the suffix is appended
# to the WHOLE filename, so `deploy.sh` -> `deploy.sh.scriptvault.yaml`).
SIDECAR_SUFFIX = ".scriptvault.yaml"

# We namespace our tags so they are visibly "ours" and easy to find/merge.
# A ScriptVault tag is just a free-form string, so `risk:high` is perfectly legal
# and becomes fuzzy-searchable in the TUI (type "risk" to surface risky scripts).
RISK_TAG_PREFIX = "risk:"
OWNER_TAG_PREFIX = "owner:"

# Bulwark's `risk` is one of these lowercase strings (verified from real output).
# We only add a noisy `[RISK: …]` badge to the description for the worrying ones —
# low-risk scripts get a searchable tag but no visual clutter in the preview pane.
BADGE_RISK_LEVELS = {"medium", "high", "critical"}

# Bulwark descriptions can be a giant wall of header-comment text. We cap what we
# borrow into `desc` so the preview pane stays readable.
MAX_DESC_LEN = 200


# =============================================================================
# STEP 1 — Run Bulwark and get its JSON.
# =============================================================================

def find_bulwark(explicit: str | None) -> str:
    """Locate the `bulwark` executable.

    Resolution order (most explicit wins):
      1. an explicit --bulwark path the user passed,
      2. `bulwark` already on the PATH (the installed case),
      3. a sibling repo's build output: ../bulwark/target/{release,debug}/bulwark
         (the "I cloned it next door but didn't install" case).

    We return a *string path* because that's what subprocess wants. Raising a
    clear error here (rather than letting subprocess fail cryptically) is the
    difference between a 2-second fix and a confusing stack trace.
    """
    # Case 1: the user told us exactly where it is. Trust but verify it exists.
    if explicit:
        if Path(explicit).is_file():
            return explicit
        sys.exit(f"bridge.py: --bulwark path does not exist: {explicit}")

    # Case 2: it's installed on the PATH. `shutil.which` mimics the shell lookup.
    on_path = shutil.which("bulwark")
    if on_path:
        return on_path

    # Case 3: fall back to a sibling checkout's build artifacts. This script lives
    # in ~/projects/toolbox-bridge/, so ../bulwark is the conventional spot.
    here = Path(__file__).resolve().parent          # …/toolbox-bridge
    sibling = here.parent / "bulwark"               # …/bulwark
    # Prefer an optimized release build over a debug build if both exist.
    for build in ("release", "debug"):
        candidate = sibling / "target" / build / "bulwark"
        if candidate.is_file():
            return str(candidate)

    # Nothing worked — explain every place we looked so the fix is obvious.
    sys.exit(
        "bridge.py: could not find the `bulwark` binary.\n"
        "Tried: --bulwark flag, PATH, and ../bulwark/target/{release,debug}/bulwark.\n"
        "Fix: `cargo install --path .` inside the bulwark repo, or pass --bulwark <path>."
    )


def run_bulwark_scan(bulwark: str, paths: list[str]) -> list[dict]:
    """Invoke `bulwark scan --json [paths...]` and parse the JSON it prints.

    We capture stdout (the JSON) and let stderr flow through to our own stderr so
    the user still sees Bulwark's own warnings (e.g. "scan path does not exist").
    """
    # Build the argument list explicitly. We never pass a shell string, so there
    # is no shell-injection surface even if a path contains spaces or quotes.
    cmd = [bulwark, "scan", "--json", *paths]

    try:
        # check=True turns a non-zero exit code into an exception we can report
        # cleanly. text=True decodes stdout/stderr as UTF-8 strings for us.
        completed = subprocess.run(
            cmd,
            check=True,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError:
        sys.exit(f"bridge.py: bulwark binary not runnable: {bulwark}")
    except subprocess.CalledProcessError as exc:
        # Bulwark ran but failed. Surface its own stderr — it knows best why.
        sys.exit(f"bridge.py: bulwark scan failed (exit {exc.returncode}):\n{exc.stderr}")

    # Pass Bulwark's stderr (warnings) through so the user isn't kept in the dark.
    if completed.stderr.strip():
        sys.stderr.write(completed.stderr)

    # Parse the JSON array. A parse error here means Bulwark's output shape
    # changed — report it plainly rather than dumping a raw json traceback.
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        sys.exit(f"bridge.py: could not parse bulwark JSON output: {exc}")


# =============================================================================
# STEP 2 — Translate ONE Bulwark entry into ScriptVault sidecar additions.
# -----------------------------------------------------------------------------
# This is the heart of the bridge and the single place the field mapping lives.
# Adding a new mapping later (or the reverse direction) means editing here only.
# =============================================================================

def bridge_tags_from(entry: dict) -> list[str]:
    """The tags THIS BRIDGE owns for a Bulwark entry: risk + owner.

    Returned in a stable order so dry-run output is deterministic (nice for
    diffing and for the user's confidence that re-running changes nothing).
    """
    tags: list[str] = []
    # `risk` is always present in Bulwark output, but we use .get() defensively
    # so a future field rename degrades to "skip" instead of crashing.
    risk = entry.get("risk")
    if risk:
        tags.append(f"{RISK_TAG_PREFIX}{risk}")
    owner = entry.get("owner")
    if owner:
        tags.append(f"{OWNER_TAG_PREFIX}{owner}")
    return tags


def desc_badge_from(entry: dict) -> str | None:
    """A short `[RISK: HIGH]` badge to append to `desc`, or None for low/no risk.

    We only badge the worrying levels — low-risk scripts shouldn't clutter the
    preview pane with a banner. The badge is uppercase for at-a-glance scanning.
    """
    risk = entry.get("risk")
    if risk in BADGE_RISK_LEVELS:
        return f"[RISK: {risk.upper()}]"
    return None


def is_bridge_tag(tag: str) -> bool:
    """True if a tag is one WE manage (risk:/owner:). Used to merge without
    clobbering: on each run we strip our old tags and re-add the current ones,
    leaving every hand-written tag exactly as the user wrote it."""
    return tag.startswith(RISK_TAG_PREFIX) or tag.startswith(OWNER_TAG_PREFIX)


def strip_our_badge(desc: str) -> str:
    """Remove any `[RISK: …]` badge WE previously appended, returning the clean
    base description. We strip-and-re-add (rather than bailing if one exists) for
    the same reason we do it with tags: if a script is reclassified HIGH->CRITICAL,
    the badge must UPDATE, not stay stale or duplicate. Mirrors `is_bridge_tag`."""
    # Remove a trailing badge plus the surrounding whitespace we added before it.
    return re.sub(r"\s*\[RISK:[^\]]*\]\s*$", "", desc).rstrip()


# =============================================================================
# STEP 3 — Merge into an existing sidecar (or build a fresh one) without harm.
# =============================================================================

def load_existing_sidecar(sidecar: Path) -> dict:
    """Read an existing `.scriptvault.yaml` into a dict, or return {} if there
    is none. A malformed sidecar is treated as 'start fresh but DON'T overwrite'
    — we warn and return a sentinel the caller uses to skip writing, mirroring
    ScriptVault's own "malformed sidecar is non-fatal" policy."""
    if not sidecar.exists():
        return {}
    try:
        # safe_load refuses to construct arbitrary Python objects — the correct
        # choice for any YAML you didn't write yourself.
        data = yaml.safe_load(sidecar.read_text()) or {}
        if not isinstance(data, dict):
            # A sidecar that isn't a mapping (e.g. a bare list) is unusable.
            raise ValueError("sidecar is not a YAML mapping")
        return data
    except (yaml.YAMLError, ValueError) as exc:
        # Don't silently destroy a file we can't understand. Signal "skip".
        sys.stderr.write(
            f"bridge.py: warning: skipping malformed sidecar {sidecar}: {exc}\n"
        )
        return {"__skip__": True}


def merge_sidecar(existing: dict, entry: dict) -> dict:
    """Produce the new sidecar dict: existing fields preserved, our risk/owner
    tags refreshed, and a risk badge appended to desc when warranted.

    This function is PURE (no I/O), which makes it trivial to test and to reuse
    for the future reverse direction. It returns a brand-new dict and never
    mutates `existing` in place — easier to reason about, no spooky side effects.
    """
    # Start from a shallow copy so we never mutate the caller's data.
    merged = dict(existing)

    # --- tags: keep all the user's tags, replace only OUR managed ones --------
    # 1. Take whatever tags already exist (default to empty list if absent).
    current_tags = list(merged.get("tags", []) or [])
    # 2. Drop any of our previously-written risk:/owner: tags (so re-runs that
    #    change a risk level update cleanly instead of accumulating duplicates).
    user_tags = [t for t in current_tags if not is_bridge_tag(t)]
    # 3. Re-append the current bridge tags. Order: user tags first (their intent
    #    leads), then our metadata.
    merged_tags = user_tags + bridge_tags_from(entry)
    if merged_tags:
        merged["tags"] = merged_tags

    # --- desc: seed from Bulwark only if empty, then (re)apply the risk badge -
    desc = merged.get("desc")
    # First, strip any badge WE added on a previous run so reclassification
    # (e.g. HIGH->CRITICAL) updates cleanly instead of leaving a stale badge.
    if desc:
        desc = strip_our_badge(desc)
    # If the sidecar has no description yet, borrow Bulwark's (truncated). We do
    # NOT overwrite a description the user already wrote — sidecar intent wins.
    if not desc:
        bulwark_desc = entry.get("description")
        if bulwark_desc:
            # Collapse whitespace (Bulwark descs can contain newlines), drop any
            # leading box-drawing/punctuation run (Bulwark headers love ───), and
            # truncate so the preview pane stays tidy.
            flat = " ".join(bulwark_desc.split())
            desc = flat.lstrip("─-—=_ ").strip()[:MAX_DESC_LEN].rstrip()
    # Append the current risk badge (if any). Because we stripped the old one
    # above, this is always correct and never duplicates.
    badge = desc_badge_from(entry)
    if badge:
        desc = f"{desc}  {badge}" if desc else badge
    if desc:
        merged["desc"] = desc

    # NOTE: we deliberately DO NOT touch `category` or `lang`. Bulwark's category
    # ("binary"/"destructive") is a different taxonomy from ScriptVault's
    # ("database"/"git"), and ScriptVault infers its own language. Mapping either
    # would destroy meaningful user data. See the design doc.
    return merged


# =============================================================================
# STEP 4 — The two output modes: report (default) and write-sidecars.
# =============================================================================

def sidecar_path_for(script_path: str) -> Path:
    """`/x/deploy.sh` -> `/x/deploy.sh.scriptvault.yaml`. We append to the whole
    filename (NOT replace the extension) to match ScriptVault's exact rule."""
    return Path(script_path + SIDECAR_SUFFIX)


def print_report(entries: list[dict]) -> None:
    """Read-only joined view: one row per script with its Bulwark risk + the
    name ScriptVault would show. Writes nothing — safe to run anytime."""
    if not entries:
        print("No scripts found by bulwark scan.")
        return

    # Compute a column width for paths so the table lines up, but cap it so one
    # very long path doesn't blow the layout out.
    path_w = min(max(len(e.get("path", "")) for e in entries), 60)

    # Header row.
    print(f"{'PATH':<{path_w}}  {'RISK':<8}  {'OWNER':<8}  DESC/NAME")
    print(f"{'-' * path_w}  {'-' * 8}  {'-' * 8}  {'-' * 9}")

    for e in entries:
        path = e.get("path", "")
        # Trim from the LEFT for over-long paths so the meaningful basename stays.
        shown = path if len(path) <= path_w else "…" + path[-(path_w - 1):]
        risk = e.get("risk", "?")
        owner = e.get("owner", "?")
        # ScriptVault would display the user's `name`, but the bridge only knows
        # Bulwark's description, so show a short version of that as the hint.
        desc = e.get("description") or ""
        desc = " ".join(desc.split())[:50]
        print(f"{shown:<{path_w}}  {risk:<8}  {owner:<8}  {desc}")

    print(f"\n{len(entries)} scripts. Run with --write-sidecars to enrich ScriptVault.")


def write_sidecars(entries: list[dict], apply: bool) -> None:
    """Translate every entry into a sidecar and either WRITE it (--apply) or just
    show what we would write (the safe default, a dry run)."""
    # Counters so we can give a clear one-line summary at the end.
    written = skipped = unchanged = 0

    for e in entries:
        script_path = e.get("path")
        if not script_path:
            continue
        # Bulwark scans every file, including the sidecars WE write. Never create
        # a sidecar for a sidecar (`foo.scriptvault.yaml.scriptvault.yaml`) — just
        # skip anything that already ends in our suffix.
        if script_path.endswith(SIDECAR_SUFFIX):
            continue
        sidecar = sidecar_path_for(script_path)

        existing = load_existing_sidecar(sidecar)
        # A malformed existing sidecar -> don't risk overwriting it.
        if existing.get("__skip__"):
            skipped += 1
            continue

        merged = merge_sidecar(existing, e)

        # If our merge produced no change, say so and write nothing (idempotency).
        if merged == existing:
            unchanged += 1
            continue

        # Render the YAML we intend to write. sort_keys=False keeps our intended
        # field order; allow_unicode keeps box-drawing/accents intact.
        rendered = yaml.safe_dump(merged, sort_keys=False, allow_unicode=True)

        if apply:
            # Actually write the sidecar next to the script.
            sidecar.write_text(rendered)
            print(f"wrote  {sidecar}")
            written += 1
        else:
            # DRY RUN: show the file and its contents, change nothing on disk.
            print(f"--- would write: {sidecar}")
            print(rendered.rstrip())
            print()
            written += 1

    # Final summary tells the user exactly what happened (or would happen).
    verb = "wrote" if apply else "would write"
    print(
        f"\n{verb} {written} sidecar(s); {unchanged} already up-to-date; "
        f"{skipped} skipped (malformed)."
    )
    if not apply and written:
        print("This was a DRY RUN. Re-run with --apply to write these files.")


# =============================================================================
# REVERSE DIRECTION (ScriptVault -> Bulwark) — intentionally a stub.
# -----------------------------------------------------------------------------
# YAGNI: we leave a clean seam but build nothing. If we ever want to push
# ScriptVault's human metadata BACK into Bulwark (e.g. generate Bulwark rules
# from ScriptVault categories), this is where it would go — reusing the same
# subprocess + pure-mapping structure as above. Until there's a real need, an
# empty hook keeps the surface honest.
# =============================================================================
def scriptvault_to_bulwark():  # noqa: D401 - placeholder
    raise NotImplementedError(
        "Reverse direction (ScriptVault -> Bulwark) is not implemented yet. "
        "It's an intentional stub; add it here when a real use case appears."
    )


# =============================================================================
# CLI — wire the flags to the functions above.
# =============================================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bridge.py",
        description="Bridge Bulwark's script classifications into ScriptVault sidecars.",
    )
    # Positional: optional scan paths, passed straight through to Bulwark.
    parser.add_argument(
        "paths",
        nargs="*",
        help="Paths to scan (passed to `bulwark scan`). Default: Bulwark's config.",
    )
    # --write-sidecars switches from report mode to sidecar mode.
    parser.add_argument(
        "--write-sidecars",
        action="store_true",
        help="Translate classifications into ScriptVault .scriptvault.yaml sidecars.",
    )
    # --apply is the safety gate: without it, --write-sidecars only dry-runs.
    parser.add_argument(
        "--apply",
        action="store_true",
        help="With --write-sidecars: actually write files (default is dry-run).",
    )
    # --bulwark lets the user point at a specific binary.
    parser.add_argument(
        "--bulwark",
        metavar="PATH",
        help="Path to the bulwark binary (default: PATH, then ../bulwark/target).",
    )
    args = parser.parse_args(argv)

    # --apply only makes sense alongside --write-sidecars; warn rather than fail
    # so a fat-fingered `bridge.py --apply` doesn't silently look like it worked.
    if args.apply and not args.write_sidecars:
        sys.stderr.write(
            "bridge.py: --apply has no effect without --write-sidecars (ignoring).\n"
        )

    # 1. Find Bulwark, 2. scan, 3. branch on the chosen mode.
    bulwark = find_bulwark(args.bulwark)
    entries = run_bulwark_scan(bulwark, args.paths)

    # Bulwark scans EVERY file, including the .scriptvault.yaml sidecars this
    # bridge writes. Drop those up front so neither mode treats a sidecar as a
    # script (no sidecar-for-a-sidecar, no sidecars cluttering the report).
    entries = [e for e in entries if not str(e.get("path", "")).endswith(SIDECAR_SUFFIX)]

    if args.write_sidecars:
        write_sidecars(entries, apply=args.apply)
    else:
        print_report(entries)
    return 0


# Standard Python entry-point guard: this block runs only when the file is
# executed directly (`./bridge.py`), not when imported as a module. That import
# path is exactly what lets the pure functions above be unit-tested.
if __name__ == "__main__":
    raise SystemExit(main())


# =============================================================================
# Learning Notes
# -----------------------------------------------------------------------------
# - SEPARATION BY DATA CONTRACT: the cleanest way to connect two tools is often
#   NOT to import one into the other, but to agree on a format at the boundary.
#   Here Bulwark's JSON and ScriptVault's YAML sidecar ARE that contract; the
#   bridge is just a translator. Either tool can be rewritten internally and the
#   bridge still works as long as the formats hold.
# - subprocess.run([...], check=True, capture_output=True, text=True) is the
#   modern, safe way to call another program: a list (never a shell string) means
#   no shell-injection; check=True turns failures into exceptions; text=True
#   gives you str instead of bytes.
# - PURE vs IMPURE functions: merge_sidecar / bridge_tags_from take data and
#   return data with no I/O. That makes them trivial to test and reuse. The
#   side-effecting parts (reading/writing files, printing) are kept at the edges.
# - IDEMPOTENCY: a tool that writes files should be safe to run repeatedly. We
#   strip-and-re-add our own tags and skip the write entirely when nothing
#   changed, so `bridge.py --write-sidecars --apply` twice == once.
# - DRY-RUN-BY-DEFAULT is the right posture for anything that writes across a
#   user's home directory: show intent first, require an explicit --apply.
# - yaml.safe_load / safe_dump (not plain load/dump): safe_* refuses to build
#   arbitrary Python objects from YAML — always use it for files you didn't write.
# - pathlib.Path over string concatenation: Path knows OS path semantics. Note
#   the one deliberate exception — we build the sidecar name with `path + suffix`
#   (string concat) because we must APPEND to the full filename, whereas
#   Path.with_suffix would REPLACE `.sh`, breaking ScriptVault's naming rule.
# =============================================================================
