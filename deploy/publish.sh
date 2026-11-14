#!/usr/bin/env bash
# Publish the working copy to the submitted repo — and prove that nothing else differs.
#
#   ./deploy/publish.sh --check                      # verify the invariant, change nothing
#   ./deploy/publish.sh                              # sync + set the link base, no commit
#   REPO_BASE=https://baggagego-xxx.run.app ./deploy/publish.sh --push
#
# The model this enforces:
#
#   ~/Hackathon/pitch-demo   is the SOURCE. The container on our host builds from it,
#                            and it is the only place anyone edits.
#   ~/BaggageGo              is the SUBMITTED REPO. It is derived, never edited.
#
# Deriving it means copying everything — and changing exactly two things, because the
# links in the repo should point at a host that stays up for whoever reads it, while
# the working copy keeps naming the host that is on the printed passes:
#
#     tickets.md   regenerated (it is generated output anyway, never hand-edited)
#     README.md    one marked line: <!--repo-base-->…<!--/repo-base-->
#
# Anything else that differs is drift — someone edited the repo directly, or a file
# was forgotten — so this script refuses to publish and names the files. That check is
# the whole point: `--check` is safe to run any time, and it is what keeps the two
# trees identical except for the parts that are meant to differ.
set -euo pipefail

SRC="${SRC:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DST="${DST:-$HOME/BaggageGo}"
PY="${PY:-$SRC/.venv/bin/python}"
REPO_BASE="${REPO_BASE:-}"
MODE=sync          # no flags = sync (and report); --check = verify only
PUSH=0
for arg in "$@"; do
    case "$arg" in
        --check) MODE=check ;;
        --sync) MODE=sync ;;
        --push) MODE=sync; PUSH=1 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

say() { printf '\n== %s ==\n' "$*"; }

[ -d "$DST/.git" ] || { echo "no repo at $DST (set DST=…)" >&2; exit 2; }
RSYNC_EXCLUDES=(
    --exclude=.git/ --exclude=.venv/ --exclude=__pycache__/
    --exclude=.pytest_cache/ --exclude='*.db-wal' --exclude='*.db-shm'
    --exclude=snapshots/
)
# diff(1) spells the same idea differently
DIFF_EXCLUDES=(
    -x .git -x .venv -x __pycache__ -x .pytest_cache
    -x '*.db-wal' -x '*.db-shm' -x snapshots
)

say "the two link boxes as they stand"
printf '  working copy : %s\n' "$(grep -o 'sophon1\.dedyn\.io\|https://[a-z0-9.-]*run\.app' "$SRC/tickets.md" | head -1)"
printf '  repo         : %s\n' "$(grep -o 'sophon1\.dedyn\.io\|https://[a-z0-9.-]*run\.app' "$DST/tickets.md" | head -1)"
printf '  repo README  : %s\n' "$(sed -n 's|.*<!--repo-base-->\(.*\)<!--/repo-base-->.*|\1|p' "$DST/README.md")"

if [ "$MODE" = "sync" ]; then
    [ -n "$REPO_BASE" ] || {
        echo
        echo "REPO_BASE is required to sync, e.g." >&2
        echo "  REPO_BASE=https://baggagego-xxxx.run.app ./deploy/publish.sh --push" >&2
        echo "(use the printed host to make the two trees identical again)" >&2
        exit 2
    }
    say "copying the working tree over the repo"
    rsync -a --delete "${RSYNC_EXCLUDES[@]}" "$SRC/" "$DST/"

    say "regenerating tickets.md against ${REPO_BASE}"
    ( cd "$DST" && BGGO_PUBLIC_BASE="$REPO_BASE" "$PY" make_ticket_links.py )

    say "pointing the README's marked line at ${REPO_BASE}"
    REPO_BASE="$REPO_BASE" DST="$DST" "$PY" - <<'PY'
import os, pathlib, re, sys
path = pathlib.Path(os.environ["DST"]) / "README.md"
base = os.environ["REPO_BASE"].rstrip("/")
text, n = re.subn(r"<!--repo-base-->.*?<!--/repo-base-->",
                  f"<!--repo-base-->{base}<!--/repo-base-->", path.read_text(), flags=re.S)
if n != 1:
    sys.exit(f"expected exactly one <!--repo-base--> marker in {path}, found {n}")
path.write_text(text)
print(f"  README marker -> {base}")
PY
fi

say "comparing the two trees"

# The two link boxes are judged semantically, not by filename: each tree's own base
# is normalised to <BASE>, and the files must then be byte-identical. A README that
# differs for any *other* reason is drift, not a link difference.
marker() { sed -n 's|.*<!--repo-base-->\(.*\)<!--/repo-base-->.*|\1|p' "$1" | head -1; }
# Erase BOTH bases, so what is left is the structure: the files must match exactly.
normalise() { sed "s|$SRC_BASE|<BASE>|g; s|$DST_BASE|<BASE>|g" "$1"; }

SRC_BASE="$(marker "$SRC/README.md")"
DST_BASE="$(marker "$DST/README.md")"
UNEXPECTED=0
if [ -z "$SRC_BASE" ] || [ -z "$DST_BASE" ]; then
    echo "  DRIFT: no <!--repo-base--> marker found in $([ -z "$SRC_BASE" ] && echo 'the working copy' || echo 'the repo')" >&2
    UNEXPECTED=$((UNEXPECTED + 1))
else
    printf '  working copy base: %s\n  repo base        : %s\n' "$SRC_BASE" "$DST_BASE"
    for f in README.md tickets.md; do
        if [ "$(normalise "$SRC/$f")" = "$(normalise "$DST/$f")" ]; then
            echo "  ok (identical once the link base is normalised)  $f"
        else
            echo "  DRIFT (differs beyond the link base)            $f" >&2
            UNEXPECTED=$((UNEXPECTED + 1))
        fi
    done
fi

# Everything else must be byte-identical — the link boxes are the only exceptions.
while IFS= read -r line; do
    case "$line" in
        "Files "*" differ"*)
            rel="${line#Files }"; rel="${rel%% and *}"; rel="${rel#"$SRC"/}"
            case "$rel" in
                README.md|tickets.md) : ;;   # already judged above
                *) echo "  DRIFT           $rel" >&2; UNEXPECTED=$((UNEXPECTED + 1)) ;;
            esac ;;
        "Only in "*)
            echo "  DRIFT (one side only): $line" >&2
            UNEXPECTED=$((UNEXPECTED + 1)) ;;
    esac
done < <(diff -rq "${DIFF_EXCLUDES[@]}" "$SRC" "$DST" 2>&1 || true)

if [ "$UNEXPECTED" -gt 0 ]; then
    cat >&2 <<'MSG'

The two trees differ in something that is not a link box. That is drift, and
publishing would hide it. Fix it at the source (edit only in the working copy,
then re-run), or if the repo really should differ there, change this script's
ALLOWED list deliberately — in the same commit that explains why.
MSG
    exit 1
fi
echo "  nothing else differs"

if [ "$MODE" = "sync" ]; then
    say "the repo's new state"
    git -C "$DST" status --short
    if [ "$PUSH" = "1" ]; then
        say "committing and pushing"
        git -C "$DST" add -A
        if git -C "$DST" diff --cached --quiet; then
            echo "  nothing to commit"
        else
            git -C "$DST" -c user.name="Fidexy" -c user.email="fidexy@users.noreply.github.com" \
                commit -q -m "publish: link base -> ${REPO_BASE%/}"
            git -C "$DST" push origin HEAD
        fi
    else
        echo
        echo "  (no --push: review, then commit by hand or re-run with --push)"
    fi
fi
