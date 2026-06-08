#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment-dependent
    sys.exit(
        "bridge.py needs PyYAML. Install it with:  pip install pyyaml\n"
        "(PyYAML is how we read & write ScriptVault's .scriptvault.yaml sidecars.)"
    )

SIDECAR_SUFFIX = ".scriptvault.yaml"
RISK_TAG_PREFIX = "risk:"
OWNER_TAG_PREFIX = "owner:"
BADGE_RISK_LEVELS = {"medium", "high", "critical"}
MAX_DESC_LEN = 200


def find_bulwark(explicit: str | None) -> str:
    """Locate the `bulwark` executable."""
    if explicit:
        if Path(explicit).is_file():
            return explicit
        sys.exit(f"bridge.py: --bulwark path does not exist: {explicit}")

    on_path = shutil.which("bulwark")
    if on_path:
        return on_path

    here = Path(__file__).resolve().parent
    sibling = here.parent / "bulwark"
    for build in ("release", "debug"):
        candidate = sibling / "target" / build / "bulwark"
        if candidate.is_file():
            return str(candidate)

    sys.exit(
        "bridge.py: could not find the `bulwark` binary.\n"
        "Tried: --bulwark flag, PATH, and ../bulwark/target/{release,debug}/bulwark.\n"
        "Fix: `cargo install --path .` inside the bulwark repo, or pass --bulwark <path>."
    )


def run_bulwark_scan(bulwark: str, paths: list[str]) -> list[dict]:
    """Invoke `bulwark scan --json [paths...]` and parse its JSON output."""
    cmd = [bulwark, "scan", "--json", *paths]

    try:
        completed = subprocess.run(
            cmd,
            check=True,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError:
        sys.exit(f"bridge.py: bulwark binary not runnable: {bulwark}")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"bridge.py: bulwark scan failed (exit {exc.returncode}):\n{exc.stderr}")

    if completed.stderr.strip():
        sys.stderr.write(completed.stderr)

    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        sys.exit(f"bridge.py: could not parse bulwark JSON output: {exc}")


def bridge_tags_from(entry: dict) -> list[str]:
    """Return the bridge-managed risk and owner tags for a Bulwark entry."""
    tags: list[str] = []
    risk = entry.get("risk")
    if risk:
        tags.append(f"{RISK_TAG_PREFIX}{risk}")
    owner = entry.get("owner")
    if owner:
        tags.append(f"{OWNER_TAG_PREFIX}{owner}")
    return tags


def desc_badge_from(entry: dict) -> str | None:
    """Return a risk badge for medium, high, or critical entries."""
    risk = entry.get("risk")
    if risk in BADGE_RISK_LEVELS:
        return f"[RISK: {risk.upper()}]"
    return None


def is_bridge_tag(tag: str) -> bool:
    """Return true for bridge-managed risk or owner tags."""
    return tag.startswith(RISK_TAG_PREFIX) or tag.startswith(OWNER_TAG_PREFIX)


def strip_our_badge(desc: str) -> str:
    """Remove a trailing bridge-managed risk badge from a description."""
    return re.sub(r"\s*\[RISK:[^\]]*\]\s*$", "", desc).rstrip()


def load_existing_sidecar(sidecar: Path) -> dict:
    """Read an existing ScriptVault sidecar, or return an empty dict."""
    if not sidecar.exists():
        return {}
    try:
        data = yaml.safe_load(sidecar.read_text()) or {}
        if not isinstance(data, dict):
            raise ValueError("sidecar is not a YAML mapping")
        return data
    except (yaml.YAMLError, ValueError) as exc:
        sys.stderr.write(
            f"bridge.py: warning: skipping malformed sidecar {sidecar}: {exc}\n"
        )
        return {"__skip__": True}


def merge_sidecar(existing: dict, entry: dict) -> dict:
    """Merge Bulwark risk and owner metadata into a sidecar dict."""
    merged = dict(existing)

    current_tags = list(merged.get("tags", []) or [])
    user_tags = [t for t in current_tags if not is_bridge_tag(t)]
    merged_tags = user_tags + bridge_tags_from(entry)
    if merged_tags:
        merged["tags"] = merged_tags

    desc = merged.get("desc")
    if desc:
        desc = strip_our_badge(desc)
    if not desc:
        bulwark_desc = entry.get("description")
        if bulwark_desc:
            flat = " ".join(bulwark_desc.split())
            desc = flat.lstrip("─-—=_ ").strip()[:MAX_DESC_LEN].rstrip()
    badge = desc_badge_from(entry)
    if badge:
        desc = f"{desc}  {badge}" if desc else badge
    if desc:
        merged["desc"] = desc

    return merged


def sidecar_path_for(script_path: str) -> Path:
    """Return the ScriptVault sidecar path for a script path."""
    return Path(script_path + SIDECAR_SUFFIX)


def print_report(entries: list[dict]) -> None:
    """Print a read-only report for Bulwark entries."""
    if not entries:
        print("No scripts found by bulwark scan.")
        return

    path_w = min(max(len(e.get("path", "")) for e in entries), 60)

    print(f"{'PATH':<{path_w}}  {'RISK':<8}  {'OWNER':<8}  DESC/NAME")
    print(f"{'-' * path_w}  {'-' * 8}  {'-' * 8}  {'-' * 9}")

    for e in entries:
        path = e.get("path", "")
        shown = path if len(path) <= path_w else "…" + path[-(path_w - 1):]
        risk = e.get("risk", "?")
        owner = e.get("owner", "?")
        desc = e.get("description") or ""
        desc = " ".join(desc.split())[:50]
        print(f"{shown:<{path_w}}  {risk:<8}  {owner:<8}  {desc}")

    print(f"\n{len(entries)} scripts. Run with --write-sidecars to enrich ScriptVault.")


def write_sidecars(entries: list[dict], apply: bool) -> None:
    """Write or preview ScriptVault sidecars for Bulwark entries."""
    written = skipped = unchanged = 0

    for e in entries:
        script_path = e.get("path")
        if not script_path:
            continue
        if script_path.endswith(SIDECAR_SUFFIX):
            continue
        sidecar = sidecar_path_for(script_path)

        existing = load_existing_sidecar(sidecar)
        if existing.get("__skip__"):
            skipped += 1
            continue

        merged = merge_sidecar(existing, e)

        if merged == existing:
            unchanged += 1
            continue

        rendered = yaml.safe_dump(merged, sort_keys=False, allow_unicode=True)

        if apply:
            sidecar.write_text(rendered)
            print(f"wrote  {sidecar}")
            written += 1
        else:
            print(f"--- would write: {sidecar}")
            print(rendered.rstrip())
            print()
            written += 1

    verb = "wrote" if apply else "would write"
    print(
        f"\n{verb} {written} sidecar(s); {unchanged} already up-to-date; "
        f"{skipped} skipped (malformed)."
    )
    if not apply and written:
        print("This was a DRY RUN. Re-run with --apply to write these files.")


def scriptvault_to_bulwark():  # noqa: D401
    raise NotImplementedError(
        "Reverse direction (ScriptVault -> Bulwark) is not implemented yet. "
        "It's an intentional stub; add it here when a real use case appears."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bridge.py",
        description="Bridge Bulwark's script classifications into ScriptVault sidecars.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Paths to scan (passed to `bulwark scan`). Default: Bulwark's config.",
    )
    parser.add_argument(
        "--write-sidecars",
        action="store_true",
        help="Translate classifications into ScriptVault .scriptvault.yaml sidecars.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="With --write-sidecars: actually write files (default is dry-run).",
    )
    parser.add_argument(
        "--bulwark",
        metavar="PATH",
        help="Path to the bulwark binary (default: PATH, then ../bulwark/target).",
    )
    args = parser.parse_args(argv)

    if args.apply and not args.write_sidecars:
        sys.stderr.write(
            "bridge.py: --apply has no effect without --write-sidecars (ignoring).\n"
        )

    bulwark = find_bulwark(args.bulwark)
    entries = run_bulwark_scan(bulwark, args.paths)

    entries = [e for e in entries if not str(e.get("path", "")).endswith(SIDECAR_SUFFIX)]

    if args.write_sidecars:
        write_sidecars(entries, apply=args.apply)
    else:
        print_report(entries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
