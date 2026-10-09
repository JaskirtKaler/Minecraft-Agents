# Repository organization and data preservation

Source cleanup preserves the existing Minecraft worlds, private gameplay logs, memories, credentials, and installed environments. Launchers keep using the current directories. Disk-space reclamation is a separate operation, not a side effect of organizing source code.

## Source and runtime

Track Python and Node source, package manifests, tests, local plugin source, documentation, and intentional server configuration. Ignore installed dependencies, generated server libraries, caches, world saves, logs, and plugin build output.

The pinned server and compatibility-plugin bootstrap jars remain tracked for now. Replacing those with a reproducible download manifest and explicit setup command is a later cleanup milestone. Do not remove them until fresh-checkout setup works.

## Stop tracking runtime files

Adding ignore rules does not affect files already tracked by Git. Inspect the prepared cleanup from the repository root:

```bash
bash scripts/untrack-runtime.sh --dry-run
bash scripts/untrack-runtime.sh --apply
git diff --cached --stat
```

The script targets exactly `server/cache`, `server/libraries`, `server/logs`, `server/versions`, `server/world`, `server/world_nether`, and `server/world_the_end`. It refuses to overwrite staged changes in those paths, checks that tracked files exist locally, removes only their index entries, and checks that local files remain. It does not commit, alter other staged changes, or erase old Git history.

The Codex workspace cannot write `.git/index` in this session, so apply must run in the user's terminal. On October 8 the user applied it: all 190 staged runtime removals were checked and every local file remained present. Include the ignore rules with the cleanup commit so generated files stay untracked; no commit or history rewrite was performed by Codex.

## Private data

Keep `.env`, `data/`, `scratch/`, `.venv/`, Conda, and `bot/node_modules/` intact. Retained `data/practice/` and Minecraft `data/rl/` runs can occupy significant space because they include disposable server runtimes and worlds. RL runs also keep policy checkpoints and traces. Do not automatically remove unsuccessful runs; they are debugging evidence. Before a future disk cleanup, identify exact runs to archive and preserve their reports and traces.

Previously committed worlds/logs remain in Git history after untracking. Do not rewrite history or publish the repository without reviewing that history and any player-identifying runtime configuration. Local operator lists and offline-mode settings are not suitable authentication for a public server.

## Fresh checkout

A fresh checkout will not include generated caches or world saves after the index cleanup is committed. Install the Python and Node dependencies, review and accept Minecraft's EULA yourself, and start the pinned Purpur server once to populate its runtime libraries. Stop it using the console `stop` command. The root launcher can then compile the local plugins against those libraries and start the normal stack. No existing world is moved or reset by the cleanup.
