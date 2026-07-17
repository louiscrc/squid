# Commit plan (squid → GitHub)

Do **not** commit everything in one blob. Use this order so history stays reviewable.
Run each step from the repo root after staging only the listed paths.

Do **not** commit: `AGENTS.md`, `plan.md`, `giphy.key`, `last_gif.*`, `dist/`, `build/`, `target/`, `.venv/`, or `scripts/probe_*` / `scripts/bench_*` / `scripts/smoke_*` scratch tools.

`AGENTS.md` / `plan.md` are local-only (listed in `.gitignore`). They were scrubbed from git history — do not re-add them.

---

## 1. Docs: README + MONITOR screenshot + gitignore hygiene

**Why:** Public-facing description of the fork vs upstream; ignore local agent notes and probe scratch.

```bash
git add README.md images/monitor-mode.jpeg .gitignore

git commit -m "$(cat <<'EOF'
Document squid fork differences and MONITOR mode.

Replace the upstream roadmap README with current architecture, install, and a Kraken MONITOR photo.
EOF
)"
```

---

## 2. Plugin: Lighting group + pause/version polish

**Why:** SignalRGB UI change is independent of the Python bridge.

```bash
git add SignalRGBPlugin/KrakenLCDBridge.js

git commit -m "$(cat <<'EOF'
Move device controls into Lighting; keep FPS in Settings.

SignalRGB maps property group \"lighting\" to the Lighting section; FPS stays ungrouped.
EOF
)"
```

*(If this file also contains uncommitted pause/hardening from earlier work, that is fine in the same commit — it is still plugin-only.)*

---

## 3. Core: metrics, FPS idle → `--`, compose, editors

**Why:** Main product behavior (HUD metrics, sticky FPS fix, Rust compose, GIF key handling, localhost bind, etc.).

```bash
git add \
  afterburner.py \
  signalrgb.py \
  overlay_layout.py \
  rust/lib.rs \
  overlay_editor/gif.html \
  overlay_editor/gif.js \
  scripts/start-bridge-interactive.ps1 \
  tests/test_metrics.py

git commit -m "$(cat <<'EOF'
Improve MONITOR metrics and clear FPS when the feed goes idle.

Prefer live RTSS over sticky MAHM Framerate, ignore frozen/demoted RTSS samples, and show -- when nothing is rendering. Includes Afterburner/RAM/VRAM sourcing, Rust compose encode, GIF editor key handling, and interactive Session-1 bridge start for the packaged exe.
EOF
)"
```

---

## 4. CI: Windows exe build on GitHub Actions

**Why:** Automate `SignalRGBLCDBridge.exe` for `main` / PRs / version tags.

```bash
git add build.ps1 .github/workflows/build.yml commit.md

git commit -m "$(cat <<'EOF'
Add Windows CI that builds SignalRGBLCDBridge.exe.

PyInstaller names the artifact SignalRGBLCDBridge; tags v* attach the exe to the GitHub Release. commit.md documents the intended commit split.
EOF
)"
```

---

## After the four commits

```bash
git push -u origin main
# Optional release binary:
git tag v1.0.0   # or next version
git push origin v1.0.0
```

If history was rewritten (e.g. to scrub local docs), you need a **force push**: `git push --force-with-lease origin main`.

The `release` job uploads `dist/SignalRGBLCDBridge.exe` to that tag’s GitHub Release.

---

## Sanity checks before push

1. `PYTHONPATH=. python -m pytest tests/test_metrics.py -q` (Mac OK)
2. No secrets: `giphy.key` ignored; no hardcoded Giphy keys in `overlay_editor/gif.js`
3. `git log -- AGENTS.md plan.md` is empty on the branch you push
4. `git status` clean except intentional leftovers (probe scripts stay untracked)
