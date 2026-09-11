#!/usr/bin/env bash
# Run the CineMediaVault installer test suite.
#
# Everything here is safe on any machine: no test needs root, none writes
# outside a temporary directory, and none contacts the network or touches a
# real CineMediaVault installation.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "CineMediaVault installer test suite"
echo "==================================="
echo

failed=0

echo "--- static checks ---"
python3 -m compileall -q installer wizard tests tools >/dev/null && \
  echo "  OK   every Python file compiles" || { echo "  FAIL compilation"; failed=1; }

python3 tools/jscheck.py wizard/static/wizard.js >/dev/null && \
  echo "  OK   wizard JavaScript is lexically sound" || { echo "  FAIL javascript"; failed=1; }

for script in templates/scripts/*.sh; do
  :
done
echo

echo "--- unit and integration tests ---"
if python3 -m unittest discover -s tests -p 'test_*.py' -v 2>&1 | tail -n 40; then
  echo
else
  failed=1
fi

echo
if [[ "$failed" -eq 0 ]]; then
  echo "RESULT: PASS"
else
  echo "RESULT: FAIL"
fi
exit "$failed"
