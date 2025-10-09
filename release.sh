#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./package-swarm.sh            -> git-archive from HEAD, output SWARM-<tag|commit|date>.zip
#   ./package-swarm.sh --working  -> zip working tree (includes uncommitted)
#   ./package-swarm.sh --strip-secrets -> remove .env and resources/wallet*.txt from archive
#   ./package-swarm.sh --working --strip-secrets custom-name.zip

WORKING=false
STRIP=false
OUTFILE=""
while [[ ${1:-} != "" ]]; do
  case "$1" in
    --working) WORKING=true ;;
    --strip-secrets) STRIP=true ;;
    --help) echo "Usage: $0 [--working] [--strip-secrets] [output.zip]"; exit 0 ;;
    *) OUTFILE="$1" ;;
  esac
  shift
done

# Repo name & version
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$(pwd)")
REPO_NAME=$(basename "$REPO_ROOT")
VERSION=$(git -C "$REPO_ROOT" describe --tags --always 2>/dev/null || date +%Y%m%d-%H%M%S)

if [[ -z "$OUTFILE" ]]; then
  OUTFILE="${REPO_NAME}-${VERSION}.zip"
fi

TMPDIR=$(mktemp -d)
cleanup(){ rm -rf "$TMPDIR"; }
trap cleanup EXIT

if ! $WORKING; then
  # Try git archive -> produces a zip containing tracked files
  if git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    echo "Creating archive from Git HEAD (tracked files only)..."
    git -C "$REPO_ROOT" archive --format=tar HEAD | tar -x -C "$TMPDIR"
  else
    echo "Not a git repo — switching to working-tree mode."
    WORKING=true
  fi
fi

if $WORKING; then
  echo "Zipping working tree (includes uncommitted) into temp..."
  # copy working tree contents into TMPDIR but preserve names and skip .git
  rsync -a --exclude='.git' --exclude="${OUTFILE##*/}" "$REPO_ROOT"/ "$TMPDIR"/
fi

if $STRIP; then
  echo "Removing common secret files from temp before packaging..."
  rm -f "$TMPDIR"/.env "$TMPDIR"/env.* 2>/dev/null || true
  find "$TMPDIR/resources" -type f -name "wallet*.txt" -delete 2>/dev/null || true
  # add any extra patterns you want removed:
  find "$TMPDIR" -type f -name "*.pem" -o -name "*.key" -delete 2>/dev/null || true
fi

# Create zip
echo "Creating $OUTFILE ..."
# Use zip if available for better compression; fallback to python zip
pushd "$TMPDIR" >/dev/null
if command -v zip >/dev/null 2>&1; then
  zip -r --symlinks "$REPO_ROOT/$OUTFILE" . >/dev/null
else
  python3 - <<PY
import zipfile,os
out = os.path.join("$REPO_ROOT","$OUTFILE")
with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
    for root,dirs,files in os.walk("."):
        for f in files:
            path = os.path.join(root,f)
            z.write(path, arcname=os.path.relpath(path,"."))
print("wrote", out)
PY
fi
popd >/dev/null

echo "Done: $OUTFILE"
