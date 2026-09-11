"""Preflight checks.

Every check is read-only. Running the full preflight suite never changes the
system, which is what makes ``--dry-run`` meaningful: it is the real validation
path, not a simulation of one.

Checks return :class:`CheckResult` objects with three outcomes:

``pass``    the requirement is satisfied
``warn``    the installation can proceed but the operator should know
``fail``    the installation cannot proceed as configured
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

PASS = "pass"
WARN = "warn"
FAIL = "fail"


@dataclass
class CheckResult:
    check_id: str
    title: str
    status: str
    detail: str = ""
    remedy: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status != FAIL

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.check_id,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "remedy": self.remedy,
            "data": self.data,
        }


def ok(check_id: str, title: str, detail: str = "", **data) -> CheckResult:
    return CheckResult(check_id, title, PASS, detail, data=data)


def warn(check_id: str, title: str, detail: str, remedy: str = "", **data) -> CheckResult:
    return CheckResult(check_id, title, WARN, detail, remedy, data=data)


def fail(check_id: str, title: str, detail: str, remedy: str = "", **data) -> CheckResult:
    return CheckResult(check_id, title, FAIL, detail, remedy, data=data)


def run_all(ctx) -> list[CheckResult]:
    """Run every preflight check against *ctx* and return the results."""
    from . import system, network, storage, gpu, deps

    results: list[CheckResult] = []
    for module in (system, deps, network, storage, gpu):
        results.extend(module.checks(ctx))
    return results


def summarise(results: list[CheckResult]) -> dict[str, Any]:
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.status == PASS),
        "warnings": sum(1 for r in results if r.status == WARN),
        "failures": sum(1 for r in results if r.status == FAIL),
        "can_install": all(r.ok for r in results),
        "results": [r.to_json() for r in results],
    }
