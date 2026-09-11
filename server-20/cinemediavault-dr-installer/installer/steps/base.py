"""Step framework.

A step is a named, idempotent unit of installation. Each declares:

``id``            stable identifier used in the journal and on the CLI
``title``         one line the wizard shows
``description``   what it will do, for the preview screen
``depends_on``    fingerprint inputs - when any change, the step re-runs

``applies(ctx)``  whether this configuration needs the step at all
``run(ctx)``      does the work and returns a :class:`StepResult`

Steps must be safe to re-enter. The convention throughout is *converge, don't
create*: check the desired state, change only what differs, and report what
changed. That is what lets the installer resume after a reboot and what makes a
second run a repair rather than a duplicate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class StepResult:
    changed: bool = False
    summary: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> "StepResult":
        self.warnings.append(message)
        return self


class Step:
    id: str = ""
    title: str = ""
    description: str = ""
    #: Configuration keys whose values form this step's fingerprint.
    depends_on: tuple[str, ...] = ()
    #: Steps that must have completed first. Used only for ordering validation.
    requires: tuple[str, ...] = ()
    #: True when the step writes nothing and is safe in dry-run.
    read_only: bool = False

    def applies(self, ctx) -> bool:      # pragma: no cover - trivial default
        return True

    def fingerprint_inputs(self, ctx) -> list[Any]:
        return [ctx.get(key) for key in self.depends_on]

    def run(self, ctx) -> StepResult:    # pragma: no cover - abstract
        raise NotImplementedError

    def preview(self, ctx) -> str:
        return self.description or self.title

    def __repr__(self) -> str:           # pragma: no cover - debugging aid
        return f"<Step {self.id}>"


def ordered_steps() -> list[Step]:
    """Every step, in execution order.

    Ordering is explicit rather than derived, because the correct sequence is a
    design decision (users before directories, directories before payload,
    payload before services) and an implicit topological sort would hide it.
    """
    from . import (
        s010_packages, s020_users, s030_directories, s040_mounts, s050_payload,
        s060_config, s070_tls, s080_database, s090_admin, s100_modules,
        s110_creators, s120_epg, s130_subtitles, s135_promo, s140_transcode,
        s150_systemd, s160_timers, s170_logrotate, s180_firewall, s190_start,
        s200_verify,
    )
    modules = (
        s010_packages, s020_users, s030_directories, s040_mounts, s050_payload,
        s060_config, s070_tls, s080_database, s090_admin, s100_modules,
        s110_creators, s120_epg, s130_subtitles, s135_promo, s140_transcode,
        s150_systemd, s160_timers, s170_logrotate, s180_firewall, s190_start,
        s200_verify,
    )
    steps: list[Step] = []
    for module in modules:
        steps.extend(module.steps())
    _validate_order(steps)
    return steps


def _validate_order(steps: Iterable[Step]) -> None:
    seen: set[str] = set()
    for step in steps:
        if not step.id:
            raise ValueError(f"{step!r} has no id")
        if step.id in seen:
            raise ValueError(f"duplicate step id: {step.id}")
        for requirement in step.requires:
            if requirement not in seen:
                raise ValueError(
                    f"step {step.id} requires {requirement}, which does not run before it")
        seen.add(step.id)
