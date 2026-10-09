#!/usr/bin/env bash
# Change only the Git index. Never delete, move, or reset local runtime files.
set -euo pipefail
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
cd "$ROOT_DIR"
MODE="${1:---dry-run}"
case "$MODE" in
    --dry-run|--apply) ;;
    --help|-h)
        echo "Usage: bash scripts/untrack-runtime.sh [--dry-run|--apply]"
        echo "Stops tracking seven generated server directories; keeps all local files."
        exit 0 ;;
    *) echo "Usage: bash scripts/untrack-runtime.sh [--dry-run|--apply]" >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { echo "Only one option is accepted." >&2; exit 2; }
git rev-parse --is-inside-work-tree >/dev/null
TARGETS=(server/cache server/libraries server/logs server/versions server/world server/world_nether server/world_the_end)
TRACKED=()
while IFS= read -r -d '' path; do TRACKED+=("$path"); done < <(git ls-files -z -- "${TARGETS[@]}")
if [ "${#TRACKED[@]}" -eq 0 ]; then
    echo "No generated runtime files remain tracked. Local data is unchanged."
    exit 0
fi
for path in "${TRACKED[@]}"; do
    [ -f "$path" ] || { echo "Missing tracked local file: $path. Resolve it before cleanup." >&2; exit 1; }
done
# Never discard someone else's staged changes, even when targeting runtime data.
git diff --cached --quiet -- "${TARGETS[@]}" || {
    echo "Runtime paths contain staged changes. Review them before cleanup." >&2; exit 1;
}
echo "Generated runtime files tracked: ${#TRACKED[@]}"
printf '  %s\n' "${TARGETS[@]}"
if [ "$MODE" = --dry-run ]; then
    echo "Dry run only. Use --apply to stage removal from Git tracking, keeping local files."
    exit 0
fi
git rm -r --cached --ignore-unmatch -- "${TARGETS[@]}" >/dev/null
for path in "${TRACKED[@]}"; do
    [ -f "$path" ] || { echo "Unexpected missing local file: $path" >&2; exit 1; }
done
echo "Removed ${#TRACKED[@]} files from Git tracking; every local file is still present."
echo "Review git diff --cached --stat before committing. Old Git history is unchanged."
