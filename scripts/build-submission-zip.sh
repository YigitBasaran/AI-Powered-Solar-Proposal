#!/usr/bin/env bash
#
# Build the submission archive.
#
# Uses `git archive` so the contents are exactly what is tracked — which means
# .gitignore is the single definition of what ships, and there is no second
# exclude list to drift out of sync with it. Untracked build output, virtual
# environments, node_modules, databases, .env and Ollama weights are therefore
# excluded by construction rather than by a hand-maintained list.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

name="solarvis-ai-proposal"
out_dir="$root/submission"
archive="$out_dir/$name.zip"

mkdir -p "$out_dir"
rm -f "$archive"

echo "== pre-flight =="

if [ -n "$(git status --porcelain)" ]; then
  echo "  ! working tree is dirty; the archive will reflect HEAD, not your edits"
  git status --short | sed 's/^/    /'
fi

# A committed .env would be a credential leak, and git archive would happily
# include it. Fail loudly rather than shipping it.
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "  FAIL: .env is tracked by git. Remove it before packaging." >&2
  exit 1
fi

echo "  scanning tracked files for secrets..."
leaks=$(git ls-files -z \
  | xargs -0 grep -lIE '(AIza[0-9A-Za-z_-]{30,}|sk-[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY)' \
    2>/dev/null || true)
if [ -n "$leaks" ]; then
  echo "  FAIL: possible secrets in:" >&2
  echo "$leaks" | sed 's/^/    /' >&2
  exit 1
fi
echo "  no secrets found"

# The sample PDF is a required deliverable and is easy to forget to regenerate.
if [ ! -s sample-output/example-proposal.pdf ]; then
  echo "  FAIL: sample-output/example-proposal.pdf is missing or empty." >&2
  echo "        Generate it from the running application first." >&2
  exit 1
fi

echo "== archiving =="
git archive --format=zip --prefix="$name/" -o "$archive" HEAD
size=$(du -h "$archive" | cut -f1)
count=$(git ls-files | wc -l | tr -d ' ')

echo "  $archive"
echo "  $count tracked files, $size"

echo "== contents sanity =="
for required in \
  "$name/README.md" \
  "$name/docker-compose.yml" \
  "$name/.env.example" \
  "$name/LICENSE-NOTICE.md" \
  "$name/apps/api/pyproject.toml" \
  "$name/apps/web/package.json" \
  "$name/apps/api/app/data/fixed_roof_calibration.json" \
  "$name/fixtures/maps/satellite-fixture.png" \
  "$name/sample-output/example-proposal.pdf" \
  "$name/docs/location-verification.md" \
  "$name/docs/case-questions.md"
do
  if unzip -l "$archive" "$required" >/dev/null 2>&1; then
    echo "  ok   $required"
  else
    echo "  MISSING $required" >&2
    exit 1
  fi
done

echo "== must NOT be present =="
# Anchored patterns. A loose match on "\.env" also matches .env.example, which
# is a required file - the check has to distinguish them.
listing=$(unzip -Z1 "$archive")
check_absent() {
  local label="$1" pattern="$2"
  if printf '%s\n' "$listing" | grep -qE "$pattern"; then
    echo "  FAIL: archive contains $label" >&2
    printf '%s\n' "$listing" | grep -E "$pattern" | head -5 | sed 's/^/    /' >&2
    exit 1
  fi
  echo "  ok   no $label"
}

check_absent ".env"          '(^|/)\.env$'
check_absent "node_modules"  '(^|/)node_modules/'
check_absent "virtualenvs"   '(^|/)\.venv/'
check_absent "databases"     '\.(db|sqlite3?)$'
check_absent "model weights" '\.gguf$'
check_absent "next build"    '(^|/)\.next/'
check_absent "python cache"  '(^|/)__pycache__/'

echo
echo "Archive ready. Verify it from a clean extraction with:"
echo "  bash scripts/verify-submission.sh"
