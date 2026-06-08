# toolbox-bridge

**A small, read-then-write middleman that feeds [Bulwark](https://github.com/tom2025b/bulwark)'s
script classifications into [ScriptVault](https://github.com/tom2025b/scriptvault).**

Bulwark and ScriptVault stay completely separate tools. This bridge does not
import either of them — it talks to Bulwark only through its CLI, and to
ScriptVault only by writing the sidecar files ScriptVault already knows how to
read. The only thing shared is a data format.

```
bulwark scan --json  ─→  bridge.py  ─→  <script>.scriptvault.yaml  ─→  ScriptVault
   (Rust, read-only)      (translator)        (YAML sidecar)            reads on next scan
```

## What it does

- Runs `bulwark scan --json` and parses the classification of every script.
- Translates Bulwark's **risk** and **owner** into ScriptVault metadata:
  - searchable tags: `risk:high`, `owner:user`
  - a `[RISK: HIGH]` badge appended to the script's `desc` (medium/high/critical only)
- Writes that into each script's `<script>.scriptvault.yaml` sidecar — so the
  badges appear right inside ScriptVault's search results and preview pane.

It deliberately does **not** map Bulwark's `category` (`binary`/`destructive`)
onto ScriptVault's `category` (`database`/`git`) — they're different taxonomies,
and overwriting one with the other would destroy meaningful grouping.

## Safety

- **Dry-run by default.** `--write-sidecars` shows what it *would* write.
  You must add `--apply` to actually create files.
- **Non-clobbering & idempotent.** It reads any existing sidecar and only
  manages its own `risk:`/`owner:` tags and the `[RISK: …]` badge. Your
  hand-written `name`, `usage`, and other tags are never touched. Running it
  twice changes nothing the second time.
- **Malformed sidecars are skipped, not overwritten** (mirroring ScriptVault's
  own policy).
- **Read-only toward Bulwark** — it only invokes Bulwark's CLI.

## Install / run

Requires Python 3 and PyYAML:

```bash
pip install pyyaml
```

The bridge finds the `bulwark` binary automatically: it checks `--bulwark`, then
your `PATH`, then `../bulwark/target/{release,debug}/bulwark` (handy if you
cloned the repos side by side and didn't `cargo install`).

## Usage

```bash
./bridge.py
./bridge.py ~/bin ~/.local/bin

./bridge.py --write-sidecars

./bridge.py --write-sidecars --apply

./bridge.py --bulwark /path/to/bulwark --write-sidecars
```

After `--apply`, open ScriptVault and the risk/owner tags are searchable and the
`[RISK: …]` badge shows in the preview pane.

## Extending it

Field mapping lives in one place — `bridge_tags_from` / `merge_sidecar` in
`bridge.py`. Add a new Bulwark→ScriptVault mapping there. The reverse direction
(ScriptVault → Bulwark) is a clearly-marked stub (`scriptvault_to_bulwark`),
left unbuilt until there's a real need.
