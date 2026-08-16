#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOP="${ROOT}/rpmbuild"
mkdir -p "${TOP}/SOURCES" "${TOP}/SPECS" "${TOP}/BUILD" "${TOP}/RPMS" "${TOP}/SRPMS"
rm -rf "${TOP}/SOURCES/cinevault-portable-installer"
cp -a "${ROOT}" "${TOP}/SOURCES/cinevault-portable-installer"
cp "${ROOT}/rpm/cinevault-portable.spec" "${TOP}/SPECS/"
rpmbuild --define "_topdir ${TOP}" -bb "${TOP}/SPECS/cinevault-portable.spec"
find "${TOP}/RPMS" -type f -name '*.rpm' -print

