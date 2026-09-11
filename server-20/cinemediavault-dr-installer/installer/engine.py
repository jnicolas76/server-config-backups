"""The installation engine.

Runs the ordered steps against a context, recording each one in the journal
before and after it executes. The contract it upholds:

* **Resumable.** A step that already completed under the same configuration is
  skipped and reported. Killing the process, or rebooting, loses at most the
  step that was in flight, and re-running re-enters it safely.
* **Reversible.** Every file a step replaces is snapshotted into the run's
  backup set first, and the journal records where. ``rollback`` replays those
  snapshots in reverse.
* **Observable.** Progress is published as structured events, so the CLI and
  the wizard show the same thing without either one polling files.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .core.errors import InstallerError, StepError
from .core.journal import COMPLETED, SKIPPED, fingerprint
from .steps.base import Step, StepResult, ordered_steps


@dataclass
class RunReport:
    started_at: float = 0.0
    finished_at: float = 0.0
    ok: bool = False
    executed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    not_applicable: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failed_step: str = ""
    error: str = ""
    service_urls: dict[str, str] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(0.0, self.finished_at - self.started_at)

    def to_json(self) -> dict:
        return {
            "ok": self.ok,
            "duration": round(self.duration, 1),
            "executed": self.executed,
            "skipped": self.skipped,
            "not_applicable": self.not_applicable,
            "changed": self.changed,
            "warnings": self.warnings,
            "failed_step": self.failed_step,
            "error": self.error,
            "service_urls": self.service_urls,
        }


ProgressCallback = Callable[[dict], None]


class Engine:
    def __init__(self, ctx, *, steps: Iterable[Step] | None = None,
                 on_progress: ProgressCallback | None = None):
        self.ctx = ctx
        self.steps: list[Step] = list(steps) if steps is not None else ordered_steps()
        self.on_progress = on_progress

    # ------------------------------------------------------------------
    def plan(self) -> list[dict]:
        """What a run would do, without doing any of it."""
        ctx = self.ctx
        journal = ctx.journal
        plan: list[dict] = []
        for step in self.steps:
            if not self._selected(step):
                continue
            applies = step.applies(ctx)
            mark = fingerprint(step.id, *step.fingerprint_inputs(ctx))
            will_run = applies and journal.should_run(
                step.id, mark, force=self._forced(step))
            plan.append({
                "id": step.id,
                "title": step.title,
                "applies": applies,
                "will_run": will_run,
                "status": journal.record(step.id).status,
                "preview": step.preview(ctx) if applies else "not applicable",
            })
        return plan

    # ------------------------------------------------------------------
    def run(self) -> RunReport:
        ctx = self.ctx
        report = RunReport(started_at=time.time())
        selected = [s for s in self.steps if self._selected(s)]
        journal = ctx.journal
        journal.begin_run(
            installer_version=ctx.installer_version,
            config_hash=ctx.config_hash,
            order=[s.id for s in selected],
        )

        self._emit({"event": "run_started", "steps": len(selected),
                    "dry_run": ctx.dry_run})

        try:
            for index, step in enumerate(selected, 1):
                self._run_one(step, index, len(selected), report)
        except StepError as exc:
            report.failed_step = exc.step or ""
            report.error = str(exc)
            report.ok = False
            report.finished_at = time.time()
            journal.finish_run(ok=False)
            self._emit({"event": "run_failed", "step": report.failed_step,
                        "error": str(exc)})
            return report
        except InstallerError as exc:
            report.error = str(exc)
            report.ok = False
            report.finished_at = time.time()
            journal.finish_run(ok=False)
            self._emit({"event": "run_failed", "error": str(exc)})
            return report

        report.ok = True
        report.finished_at = time.time()
        report.service_urls = dict(ctx.service_urls)
        journal.finish_run(ok=True)
        self._emit({"event": "run_finished", "ok": True,
                    "duration": round(report.duration, 1)})
        return report

    # ------------------------------------------------------------------
    def _run_one(self, step: Step, index: int, total: int, report: RunReport) -> None:
        ctx = self.ctx
        journal = ctx.journal

        if not step.applies(ctx):
            journal.skip_step(step.id, summary="not applicable to this configuration")
            report.not_applicable.append(step.id)
            ctx.logger.debug(f"[{index}/{total}] {step.id}: not applicable")
            self._emit({"event": "step_skipped", "step": step.id, "index": index,
                        "total": total, "reason": "not applicable"})
            return

        mark = fingerprint(step.id, *step.fingerprint_inputs(ctx))
        if not journal.should_run(step.id, mark, force=self._forced(step)):
            record = journal.record(step.id)
            report.skipped.append(step.id)
            ctx.logger.info(f"[{index}/{total}] {step.title}: already done"
                            + (f" ({record.summary})" if record.summary else ""))
            self._emit({"event": "step_skipped", "step": step.id, "index": index,
                        "total": total, "reason": "already completed",
                        "summary": record.summary})
            return

        ctx.logger.info(f"[{index}/{total}] {step.title}")
        self._emit({"event": "step_started", "step": step.id, "title": step.title,
                    "index": index, "total": total})
        journal.start_step(step.id, mark)

        # Each step gets its own backup slice, so a rollback can stop at the
        # step that failed rather than undoing everything indiscriminately.
        before = len(ctx.backups.entries)
        started = time.monotonic()
        try:
            result = step.run(ctx) or StepResult()
        except StepError:
            journal.fail_step(
                step.id, "step failed",
                backups=ctx.backups.to_json()[before:])
            raise
        except Exception as exc:                            # noqa: BLE001
            journal.fail_step(step.id, f"{type(exc).__name__}: {exc}",
                              backups=ctx.backups.to_json()[before:])
            raise StepError(f"{step.title}: {exc}", step=step.id) from exc

        elapsed = time.monotonic() - started
        journal.complete_step(
            step.id,
            summary=result.summary,
            changed=result.changed,
            backups=ctx.backups.to_json()[before:],
        )

        report.executed.append(step.id)
        if result.changed:
            report.changed.append(step.id)
        for message in result.warnings:
            report.warnings.append(f"{step.id}: {message}")
            ctx.logger.warning(message)

        if result.summary:
            ctx.logger.success(f"{step.title}: {result.summary} ({elapsed:.1f}s)")
        self._emit({"event": "step_finished", "step": step.id, "index": index,
                    "total": total, "changed": result.changed,
                    "summary": result.summary, "warnings": result.warnings,
                    "duration": round(elapsed, 2)})

    # ------------------------------------------------------------------
    def _selected(self, step: Step) -> bool:
        ctx = self.ctx
        if ctx.only_steps and not self._matches(step.id, ctx.only_steps):
            return False
        if ctx.skip_steps and self._matches(step.id, ctx.skip_steps):
            return False
        return True

    def _forced(self, step: Step) -> bool:
        return bool(self.ctx.force_steps) and self._matches(step.id, self.ctx.force_steps)

    @staticmethod
    def _matches(step_id: str, patterns: tuple[str, ...]) -> bool:
        """``config`` matches ``config`` and ``config.anything``."""
        for pattern in patterns:
            if pattern == "all":
                return True
            if step_id == pattern or step_id.startswith(pattern + "."):
                return True
        return False

    def _emit(self, event: dict) -> None:
        event.setdefault("ts", time.time())
        if self.on_progress:
            try:
                self.on_progress(event)
            except Exception:                               # noqa: BLE001
                # A broken progress consumer must never break the install.
                pass
