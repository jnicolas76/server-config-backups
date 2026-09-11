# CineMediaVault installer developer guide

For changing the installer itself.

---

## Shape of the package

```
install.sh              bootstrap: check the machine, install prerequisites,
                        hand over to the engine
installer/              the engine
  version.py              version and supported-platform constants
  cli.py                  cinevaultctl - every lifecycle command
  engine.py               runs the ordered steps against a context
  templates.py            the small template renderer
  config/
    schema.py             THE source of truth for every setting
    loader.py             YAML subset reader/writer, env overrides
    secrets.py            hashing, secret files, env-file quoting
  core/
    context.py            everything a step needs, in one object
    journal.py            resumability and idempotency ledger
    logging.py            redacting logger with a live subscriber feed
    redact.py             secret redaction
    fsops.py              atomic writes, pre-change backups
    runner.py             subprocess execution, argv only
    errors.py             typed failures
  preflight/              read-only machine checks
  steps/                  the installation steps, in order
  discovery/              HDHomeRun and mount inspection
  tooling/                backup, restore, upgrade, rollback, uninstall, smoke test
wizard/                 the first-run setup website
templates/              systemd units, Compose, logrotate, scripts
payload/                the CineMediaVault application and generators
tests/                  the test suite
tools/                  jscheck, smoke-test, source zip, doc generation
```

---

## The two ideas everything rests on

### 1. One schema

`installer/config/schema.py` declares every setting exactly once: type, default,
constraints, whether it is a secret, which wizard stage collects it, and its help
text. That single declaration drives validation, the generated documentation, the
redacted example, and which values the redactor masks.

The browser wizard and an unattended file therefore **cannot** disagree: both
call `validate()` on the same document.

Adding a setting:

```python
_f("livetv.retry_seconds", "int", default=30, stage=7,
   minimum=5, maximum=600,
   help="How long to wait before retrying a tuner that did not answer."),
```

Then, if it needs to reach the application, map it in
`installer/steps/s060_config.py::build_environment`, and regenerate the
reference:

```bash
python3 tools/gen-config-reference.py
```

### 2. Converge, don't create

Every step asks "what is the desired state, and how does the system differ?" and
changes only the difference. That is what makes a second run a repair rather than
a duplicate, and it is checked by
`tests/test_idempotency.py::RealInstallIsIdempotent`, which runs the steps twice
and asserts the second run changes nothing and every file is byte-identical.

---

## Writing a step

```python
from .base import Step, StepResult


class ConfigureThing(Step):
    id = "thing"                      # stable; appears in the journal and CLI
    title = "The thing"               # one line, shown in the wizard
    description = "What this will do."
    depends_on = ("thing.enabled", "thing.port")   # fingerprint inputs
    requires = ("config",)            # ordering assertion, checked at import

    def applies(self, ctx) -> bool:
        return bool(ctx.get("thing.enabled"))

    def preview(self, ctx) -> str:
        return f"Set up the thing on port {ctx.get('thing.port')}."

    def run(self, ctx) -> StepResult:
        changed = write_file(target, content, mode=0o640,
                             user="root", group=ctx.service_group,
                             backups=ctx.backups, dry_run=ctx.dry_run,
                             logger=ctx.logger)
        return StepResult(changed=changed, summary="thing configured")
```

Then add it to the module list in `installer/steps/base.py::ordered_steps`.

Rules a step must follow:

- **Honour `ctx.dry_run`.** Use the `fsops` helpers and `ctx.runner`, which
  already do. Never write directly with `open()`.
- **Pass `backups=ctx.backups`** to every write, or rollback cannot undo it.
- **Never build a shell string.** `ctx.runner.run(["cmd", arg])`. The test suite
  parses the source and fails the build if `shell=True` appears anywhere.
- **Be re-enterable.** A step interrupted halfway must be safe to run again.
- **Report, don't just do.** `StepResult.summary` is what the operator reads;
  `.warn()` is for things that worked but they should know about.
- **Fail loudly on anything unsafe.** Raise `StepError(..., recoverable=False)`
  rather than proceeding with a guess.

`depends_on` names the settings whose values form the step's fingerprint. When
one changes, the step re-runs; when none has, it is skipped. Get this right or
you will either never re-run or always re-run.

---

## Preflight checks

Read-only, always. `--dry-run` is the real validation path, not a simulation of
one, and that only holds if no check writes anything.

```python
def checks(ctx) -> list[CheckResult]:
    if some_condition:
        return [fail("my.check", "Title", "what is wrong", "what to do about it")]
    return [ok("my.check", "Title", "detail")]
```

`fail` blocks installation. `warn` does not. Prefer `warn` unless proceeding
would actually break something or destroy data - a heuristic that blocks an
install is worse than one that explains itself.

---

## Templates

`{{ name }}` substitutes; `{% if name %}` / `{% else %}` / `{% endif %}` include
whole lines. A referenced variable that does not exist is an error, so a typo
fails the build rather than silently emitting an empty unit directive.

Every generated systemd unit is checked by the test suite: it must parse as INI,
have no unsubstituted placeholders, run as a non-root user, carry the hardening
directives, and confine writes to the expected paths.

---

## The wizard

`wizard/server.py` is a standard-library HTTP server with a fixed route table.
It runs as root because it installs the system, so:

- it binds a private address only and **refuses** a public one;
- it requires a bootstrap token printed on the console;
- every state-changing request carries a session-bound CSRF token;
- cross-origin requests are rejected before CSRF is even checked;
- request bodies are size-capped;
- static serving is an allow-list of two files - there is no traversal surface;
- it stops itself when installation finishes and is never enabled at boot.

`wizard/api.py` maps each stage to the settings it owns. A stage can only write
its own keys, so a crafted request cannot reach `paths.install_root` from the
welcome screen. `tests/test_flows.py::WizardApi` proves it.

The front end is dependency-free by necessity: the machine being set up may have
no internet access, and an installer that needs a CDN fails exactly when you need
it most.

Adding a stage: add it to `STAGES` in `wizard/session.py`, list its fields in
`STAGE_FIELDS` in `wizard/api.py`, and add a renderer to `STAGE_RENDERERS` in
`wizard/static/wizard.js`.

---

## Secrets

Anything tagged `SECRET` in the schema is automatically:

- registered with the process-wide redactor at context creation,
- kept out of `cinemediavault.yaml` and written to `secrets.env` (0640),
- masked in the wizard, in `config show`, and in every log line.

The redactor works two ways at once: exact matching of registered values, and
pattern matching of credential *shapes*. The second is what catches a key inside
a third-party error message that the installer never saw.

The administrator password is stricter still: hashed on arrival, plaintext
overwritten immediately, never written anywhere.

---

## Tests

```bash
./tests/run-tests.sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 -m unittest tests.test_security -v
```

No test needs root, touches the network, or writes outside a temporary
directory.

| File | Covers |
|---|---|
| `test_config.py` | schema integrity, validation, YAML round-trip, env overrides |
| `test_security.py` | redaction, path safety, hashing, env quoting, shell safety |
| `test_idempotency.py` | atomic writes, journal, real double-run, permissions |
| `test_templates.py` | systemd, Compose, scripts, wizard assets |
| `test_flows.py` | dry run, mocked install, failure, rollback, uninstall, backup, migration, wizard API, CLI |

`tests/helpers.py::Sandbox` builds a complete valid configuration rooted in a
temporary directory, so the whole engine runs unprivileged.

Privileged commands are covered by `RecordingRunner`, which records argv instead
of executing - but still lets read-only probes run, because steps legitimately
branch on what they find and faking that would test a different program.

### Things worth testing when you change something

- a second run still changes nothing;
- no secret reaches any file on disk;
- a dry run creates nothing and runs nothing;
- media survives uninstall, including `--remove-generated-media`;
- generated units still parse and stay confined.

---

## Adding a payload file

Put it under `payload/`, and it is installed and hashed into
`payload-manifest.json` automatically. Anything it needs at runtime must be
mapped in `build_environment`.

If it hard-codes paths - the original generators all did - parameterise them
with environment variables at staging time and note it in the module inventory.

---

## Releasing

```bash
./tests/run-tests.sh
python3 tools/gen-config-reference.py
# bump INSTALLER_VERSION in installer/version.py
./tools/make-source-zip.sh
```

The packaging script scans for credential-shaped content and refuses to build if
it finds any.

Bump `CONFIG_SCHEMA_VERSION` only when the configuration format changes
incompatibly, and add a migration to `MIGRATIONS` in
`installer/tooling/upgrade.py` at the same time. A migration must never drop a
key it does not recognise.

---

## Design decisions worth not re-litigating

**Why no third-party Python.** The application already has none. Adding a
dependency to the installer would mean pip, a virtual environment and network
access before the installer can even check whether the machine is suitable.

**Why a hand-written YAML subset.** The alternative was requiring PyYAML before
we can read the configuration that tells us what to install. The subset is strict
- anything outside it is an error, not a silent misparse.

**Why the wizard runs as root.** Because it installs the system. The mitigations
are listed above; the alternative, a privileged helper daemon, is more moving
parts and more attack surface for the same capability.

**Why the default administrator is patched out rather than documented.** A
publicly known credential in a shipped installer is not something to warn about.
The install fails if the patch cannot be applied.

**Why the bulk transcode queue is never auto-started.** It rewrites source media.
No configuration value should be able to start that; it takes a human.
