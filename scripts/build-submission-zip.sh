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
for forbidden in ".env" "node_modules" ".venv" "*.db" "*.gguf"; do
  if unzip -l "$archive" | grep -qE "$name/.*${forbidden//./\\.}"; then
    echo "  FAIL: archive contains $forbidden" >&2
    exit 1
  fi
  echo "  ok   no $forbidden"
done

echo
echo "Archive ready. Verify it from a clean extraction with:"
echo "  bash scripts/verify-submission.sh"
