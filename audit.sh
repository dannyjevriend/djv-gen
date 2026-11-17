#!/bin/sh
# Scan the package for secrets (blocking) and personal identifiers (review).
# Exit 1 if a secret is found. Safe to run any time; wire it up as a pre-commit hook.
set -u
cd "$(dirname -- "$0")"

SECRETS='sk-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-|BEGIN [A-Z ]*PRIVATE KEY|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
IDENT="/Users/[A-Za-z]|${USER}${AUDIT_IDENT:+|$AUDIT_IDENT}"
rc=0

echo "— secrets (blocking) —"
if grep -rInE "$SECRETS" . --exclude-dir=upstream --exclude-dir=__pycache__ \
    --exclude=audit.sh --exclude=drawthings-mcp-entry.json; then
  rc=1
else
  echo "none"
fi

echo
echo "— personal identifiers (review) —"
grep -rInE "$IDENT" . --exclude-dir=upstream --exclude-dir=__pycache__ \
  --exclude=audit.sh --exclude=drawthings-mcp-entry.json || echo "none"

echo
echo "— upstream archive —"
for z in upstream/*.zip; do
  [ -f "$z" ] || continue
  tmp=$(mktemp -d)
  unzip -q "$z" -d "$tmp"
  if grep -rInE "$SECRETS" "$tmp"; then rc=1; else echo "clean: $z"; fi
  rm -rf "$tmp"
done

exit $rc
