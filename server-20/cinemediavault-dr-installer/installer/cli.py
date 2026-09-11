"""``cinevaultctl`` - the command line for every lifecycle operation.

Unattended installation, repair, upgrade, backup, restore, rollback, uninstall
and the smoke test all live here. The setup wizard drives exactly the same
engine through the same context, so a browser install and a file-driven install
cannot drift apart.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

from .config import loader
from .config.schema import (
    SCHEMA, Issue, apply_defaults, check_password, errors, get, put, validate,
    warnings as schema_warnings,
)
from .config.secrets import SecretStore, hash_password
from .core.context import Context
from .core.errors import ConfigError, InstallerError
from .core.logging import InstallLogger, set_logger
from .core.redact import REDACTOR
from .version import INSTALLER_VERSION, version_banner

DEFAULT_CONFIG = "/etc/cinemediavault/cinemediavault.yaml"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cinevaultctl",
        description=version_banner(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  cinevaultctl preflight --config my.yaml     check a machine without changing it
  cinevaultctl install --config my.yaml       unattended install
  cinevaultctl install --config my.yaml --dry-run
  cinevaultctl repair --only systemd,timers   re-converge part of an installation
  cinevaultctl status                         what is installed and running
  cinevaultctl smoke-test                     verify a running installation
  cinevaultctl backup                         archive config, databases, metadata
  cinevaultctl upgrade                        migrate and re-converge
  cinevaultctl rollback                       undo the last run
  cinevaultctl uninstall --remove-state       remove everything except media
""")
    parser.add_argument("--version", action="version", version=version_banner())
    parser.add_argument("--config", default=DEFAULT_CONFIG,
                        help=f"configuration file (default: {DEFAULT_CONFIG})")
    parser.add_argument("--log-file", default="",
                        help="write a JSON install log here")
    parser.add_argument("--log-level", default="info",
                        choices=("debug", "info", "warning", "error"))
    parser.add_argument("--json", action="store_true",
                        help="print machine-readable output instead of prose")
    parser.add_argument("--quiet", action="store_true", help="suppress console logging")

    sub = parser.add_subparsers(dest="command", required=True)

    def with_run_flags(p):
        p.add_argument("--dry-run", action="store_true",
                       help="validate and show the plan without changing anything")
        p.add_argument("--only", default="",
                       help="run only these steps (comma separated)")
        p.add_argument("--skip", default="",
                       help="skip these steps (comma separated)")
        p.add_argument("--force", default="",
                       help="re-run these steps even if already completed, "
                            "or 'all'")
        p.add_argument("--offline", action="store_true",
                       help="make no outbound network requests")
        p.add_argument("--yes", "-y", action="store_true",
                       help="do not prompt for confirmation")
        return p

    with_run_flags(sub.add_parser("install", help="install CineMediaVault"))
    with_run_flags(sub.add_parser("repair", help="re-converge an existing install"))
    with_run_flags(sub.add_parser("apply", help="regenerate configuration and units"))

    p = sub.add_parser("preflight", help="check this machine, change nothing")
    p.add_argument("--offline", action="store_true")

    p = sub.add_parser("plan", help="show what an install would do")
    p.add_argument("--offline", action="store_true")

    sub.add_parser("status", help="show what is installed and running")

    p = sub.add_parser("smoke-test", help="verify a running installation")
    p.add_argument("--deep", action="store_true",
                   help="also probe ffmpeg and the tuner (slower)")

    p = sub.add_parser("validate", help="validate a configuration file")
    p.add_argument("--strict", action="store_true",
                   help="treat warnings as failures")

    p = sub.add_parser("backup", help="archive configuration, databases and metadata")
    p.add_argument("--destination", default="")
    p.add_argument("--no-secrets", action="store_true",
                   help="exclude API keys from the archive")
    p.add_argument("--label", default="manual")

    p = sub.add_parser("restore", help="restore a backup archive")
    p.add_argument("archive")
    p.add_argument("--no-secrets", action="store_true")
    p.add_argument("--no-config", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", "-y", action="store_true")

    p = with_run_flags(sub.add_parser("upgrade", help="migrate and re-converge"))
    p.add_argument("--force-upgrade", action="store_true",
                   help="re-run even when already at this version")

    p = sub.add_parser("rollback", help="undo the last installation run")
    p.add_argument("--to-step", default="", help="stop rolling back at this step")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", "-y", action="store_true")

    p = sub.add_parser("uninstall", help="remove CineMediaVault")
    p.add_argument("--remove-state", action="store_true",
                   help="also remove the database, configuration and metadata")
    p.add_argument("--remove-generated-media", action="store_true",
                   help="also remove GENERATED libraries (comic galleries). "
                        "Source media is never removed.")
    p.add_argument("--no-keep-backups", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", "-y", action="store_true")

    p = sub.add_parser("config", help="inspect or change configuration")
    csub = p.add_subparsers(dest="config_command", required=True)
    csub.add_parser("show", help="print the effective configuration, redacted")
    csub.add_parser("example", help="print a fully commented example")
    csub.add_parser("schema", help="print the configuration schema as JSON")
    cg = csub.add_parser("get", help="read one key")
    cg.add_argument("key")
    cs = csub.add_parser("set", help="write one key")
    cs.add_argument("key")
    cs.add_argument("value")

    p = sub.add_parser("admin", help="administrator account maintenance")
    asub = p.add_subparsers(dest="admin_command", required=True)
    ap = asub.add_parser("set-password", help="set the administrator password")
    ap.add_argument("--username", default="")

    p = sub.add_parser("epg", help="extended guide collector")
    esub = p.add_subparsers(dest="epg_command", required=True)
    esub.add_parser("rebuild", help="rebuild and restart the collector")
    esub.add_parser("status", help="show collector status and guide coverage")

    p = sub.add_parser("module", help="module maintenance")
    msub = p.add_subparsers(dest="module_command", required=True)
    mr = msub.add_parser("rebuild", help="rebuild a game or comic library catalog")
    mr.add_argument("name")

    p = sub.add_parser("transcode", help="bulk library transcode queue")
    tsub = p.add_subparsers(dest="transcode_command", required=True)
    tsub.add_parser("status", help="show the queue state")
    tsub.add_parser("enable", help="arm the queue (it rewrites source media)")
    tsub.add_parser("disable", help="stop the queue")

    return parser


# --------------------------------------------------------------------------
# Configuration loading
# --------------------------------------------------------------------------

def load_config(path: str, *, required: bool = True) -> dict:
    file = Path(path)
    if not file.is_file():
        if required:
            raise ConfigError(
                f"configuration file not found: {file}\n"
                f"Create one with `cinevaultctl config example > my.yaml`, or run "
                f"./install.sh to use the setup wizard.")
        return {}
    config = loader.load(file)
    config = loader.apply_env_overrides(config)
    return apply_defaults(config)


def make_context(args, config: dict, *, logger: InstallLogger) -> Context:
    return Context(
        config,
        logger=logger,
        dry_run=bool(getattr(args, "dry_run", False)),
        only_steps=_split(getattr(args, "only", "")),
        skip_steps=_split(getattr(args, "skip", "")),
        force_steps=_split(getattr(args, "force", "")),
        package_root=PACKAGE_ROOT,
        assume_yes=bool(getattr(args, "yes", False)),
        offline=bool(getattr(args, "offline", False)),
    )


def _split(value: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in (value or "").split(",") if p.strip())


def confirm(prompt: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(f"{prompt} - refusing in a non-interactive session. Pass --yes to "
              f"proceed.", file=sys.stderr)
        return False
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_validate(args, logger) -> int:
    config = load_config(args.config)
    issues = validate(config)
    problems = errors(issues)
    notes = schema_warnings(issues)

    if args.json:
        print(json.dumps({
            "ok": not problems and (not notes or not args.strict),
            "errors": [str(i) for i in problems],
            "warnings": [str(i) for i in notes],
        }, indent=2))
    else:
        for issue in problems:
            print(f"  ERROR   {issue.key}: {issue.message}")
        for issue in notes:
            print(f"  WARNING {issue.key}: {issue.message}")
        if not issues:
            print(f"  {args.config} is valid.")
        else:
            print(f"\n  {len(problems)} error(s), {len(notes)} warning(s)")
    if problems:
        return 2
    return 3 if (notes and args.strict) else 0


def cmd_preflight(args, logger) -> int:
    from . import preflight

    config = load_config(args.config)
    ctx = make_context(args, config, logger=logger)
    results = preflight.run_all(ctx)
    summary = preflight.summarise(results)

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0 if summary["can_install"] else 3

    print()
    print("  System facts")
    for key, value in ctx.facts.summary().items():
        print(f"    {key:12} {value}")
    print()
    for result in results:
        marker = {"pass": "OK  ", "warn": "WARN", "fail": "FAIL"}[result.status]
        print(f"  [{marker}] {result.title}"
              + (f": {result.detail}" if result.detail else ""))
        if result.remedy and result.status != "pass":
            print(f"          -> {result.remedy}")
    print()
    print(f"  {summary['passed']} passed, {summary['warnings']} warnings, "
          f"{summary['failures']} failures")
    print("  " + ("Ready to install." if summary["can_install"]
                  else "Not ready: resolve the failures above."))
    print()
    return 0 if summary["can_install"] else 3


def cmd_plan(args, logger) -> int:
    from .engine import Engine

    config = load_config(args.config)
    ctx = make_context(args, config, logger=logger)
    ctx.detect_facts()
    plan = Engine(ctx).plan()

    if args.json:
        print(json.dumps(plan, indent=2))
        return 0
    print()
    for entry in plan:
        if not entry["applies"]:
            marker = "skip"
        elif entry["will_run"]:
            marker = "RUN "
        else:
            marker = "done"
        print(f"  [{marker}] {entry['title']}")
        print(f"          {entry['preview']}")
    print()
    return 0


def cmd_install(args, logger, *, repair: bool = False) -> int:
    from . import preflight
    from .engine import Engine

    config = load_config(args.config)

    # A password may be supplied in the file for a fully unattended install; it
    # is hashed here and immediately removed from the configuration so nothing
    # downstream can see or persist it.
    config = _prepare_admin_credentials(config, logger)

    issues = validate(config)
    problems = errors(issues)
    if problems:
        for issue in problems:
            logger.error(f"{issue.key}: {issue.message}")
        raise ConfigError(f"{len(problems)} configuration error(s); nothing was changed")
    for issue in schema_warnings(issues):
        logger.warning(f"{issue.key}: {issue.message}")

    ctx = make_context(args, config, logger=logger)

    results = preflight.run_all(ctx)
    summary = preflight.summarise(results)
    for result in results:
        if result.status == "fail":
            logger.error(f"{result.title}: {result.detail}")
            if result.remedy:
                logger.error(f"  -> {result.remedy}")
        elif result.status == "warn":
            logger.warning(f"{result.title}: {result.detail}")
    if not summary["can_install"]:
        raise InstallerError(
            f"{summary['failures']} preflight check(s) failed; nothing was changed")

    if not ctx.dry_run and not repair:
        logger.info(f"installing CineMediaVault {INSTALLER_VERSION}")

    report = Engine(ctx).run()

    if args.json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        _print_report(report, ctx)
    return 0 if report.ok else 4


def _prepare_admin_credentials(config: dict, logger) -> dict:
    password = get(config, "admin.password") or ""
    if not password:
        return config
    problems = check_password(password)
    if problems:
        for issue in problems:
            logger.error(f"{issue.key}: {issue.message}")
        raise ConfigError("the administrator password does not meet the policy")
    REDACTOR.register(password)
    put(config, "admin.password_hash", hash_password(password))
    # Overwrite, do not merely unset: nothing downstream should be able to
    # recover the plaintext, including a traceback or a serialised context.
    put(config, "admin.password", "")
    logger.info("administrator password hashed; the plaintext was discarded")
    return config


def _print_report(report, ctx) -> None:
    print()
    if report.ok:
        print("  Installation complete.")
    else:
        print("  Installation FAILED.")
        if report.failed_step:
            print(f"    failed step: {report.failed_step}")
        if report.error:
            print(f"    {report.error}")
    print(f"    {len(report.executed)} step(s) run, {len(report.changed)} changed, "
          f"{len(report.skipped)} already done, in {report.duration:.1f}s")
    if report.warnings:
        print()
        print(f"  {len(report.warnings)} warning(s):")
        for message in report.warnings:
            print(f"    - {message}")
    if report.ok:
        print()
        print("  Open CineMediaVault at:")
        print(f"    {ctx.primary_url()}")
        print(f"    sign in as '{ctx.get('admin.username')}'")
        print()
        print("  Next steps:")
        print("    cinevaultctl smoke-test      verify the installation")
        print("    cinevaultctl status          see what is running")
        print("    cinevaultctl backup          take the first backup")
    print()


def cmd_status(args, logger) -> int:
    config = load_config(args.config, required=False)
    ctx = make_context(args, config or {}, logger=logger)
    ctx.detect_facts()

    version = "not installed"
    installed_at = ""
    if ctx.layout.version_file.is_file():
        try:
            data = json.loads(ctx.layout.version_file.read_text(encoding="utf-8"))
            version = data.get("installer_version", "unknown")
            installed_at = data.get("installed_at", "")
        except (OSError, ValueError):
            version = "unreadable"

    units = ["cinemediavault.service", "cinemediavault-health.timer",
             "cinemediavault-refresh.timer", "cinemediavault-backup.timer",
             "cinemediavault-metadata.timer", "cinemediavault-thumbnails.timer",
             "cinemediavault-bookvault.service", "cinemediavault-subtitles.service"]
    for game in ctx.selected_games():
        units.append(f"cinemediavault-module@{game}.service")
    if ctx.get("modules.comics"):
        units.append("cinemediavault-module@comics.service")

    states = {}
    for unit in units:
        if not ctx.runner.systemd_unit_exists(unit):
            continue
        states[unit] = ("active" if ctx.runner.systemd_unit_active(unit)
                        else "inactive")

    journal = ctx.journal
    payload = {
        "installed_version": version,
        "package_version": INSTALLER_VERSION,
        "installed_at": installed_at,
        "url": ctx.primary_url() if config else "",
        "units": states,
        "journal": journal.progress() if journal.order else {},
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print()
    print(f"  CineMediaVault {version}"
          + (f"  (package {INSTALLER_VERSION})" if version != INSTALLER_VERSION else ""))
    if installed_at:
        print(f"  installed {installed_at}")
    if config:
        print(f"  {ctx.primary_url()}")
    print()
    if states:
        print("  Services")
        for unit, state in sorted(states.items()):
            print(f"    {'OK  ' if state == 'active' else 'DOWN'}  {unit}")
    else:
        print("  No CineMediaVault units are installed.")
    if journal.order:
        progress = journal.progress()
        print()
        print(f"  Last run: {progress['phase']}, "
              f"{progress['completed']}/{progress['total']} steps")
        if progress["failed"]:
            print(f"    failed: {', '.join(progress['failed'])}")
    print()
    return 0


def cmd_smoke_test(args, logger) -> int:
    from .tooling import smoketest

    config = load_config(args.config)
    ctx = make_context(args, config, logger=logger)
    ctx.detect_facts()
    report = smoketest.run(ctx, deep=bool(getattr(args, "deep", False)))

    if args.json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        print(report.render())
    return 0 if report.ok else 5


def cmd_backup(args, logger) -> int:
    from .tooling import backup as backup_tool

    config = load_config(args.config)
    ctx = make_context(args, config, logger=logger)
    archive = backup_tool.create(
        ctx,
        destination=args.destination or None,
        include_secrets=not args.no_secrets,
        label=args.label,
    )
    if args.json:
        print(json.dumps({"archive": str(archive)}, indent=2))
    return 0


def cmd_restore(args, logger) -> int:
    from .tooling import backup as backup_tool

    config = load_config(args.config)
    ctx = make_context(args, config, logger=logger)
    manifest = backup_tool.inspect(args.archive)
    print(f"  backup created {manifest.get('created_at')}")
    print(f"  contents: {', '.join(manifest.get('contents', []))}")
    if not confirm("Restore this backup over the current installation?",
                   assume_yes=args.yes or ctx.dry_run):
        return 130
    result = backup_tool.restore(ctx, args.archive,
                                 restore_secrets=not args.no_secrets,
                                 restore_config=not args.no_config)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_upgrade(args, logger) -> int:
    from .tooling import upgrade as upgrade_tool

    config = load_config(args.config)
    config = _prepare_admin_credentials(config, logger)
    ctx = make_context(args, config, logger=logger)
    ctx.detect_facts()
    result = upgrade_tool.upgrade(ctx, force=args.force_upgrade)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n  {result['from_version']} -> {result['to_version']}")
        if result["migrations"]:
            print(f"  migrations: {', '.join(result['migrations'])}")
        print(f"  {'OK' if result['ok'] else 'FAILED'}\n")
    return 0 if result["ok"] else 4


def cmd_rollback(args, logger) -> int:
    from .tooling import rollback as rollback_tool

    config = load_config(args.config, required=False)
    ctx = make_context(args, config or {}, logger=logger)
    if not confirm("Undo the last installation run?",
                   assume_yes=args.yes or ctx.dry_run):
        return 130
    result = rollback_tool.rollback(ctx, to_step=args.to_step)
    if args.json:
        print(json.dumps(result, indent=2))
    return 0


def cmd_uninstall(args, logger) -> int:
    from .tooling import rollback as rollback_tool

    config = load_config(args.config, required=False)
    ctx = make_context(args, config or {}, logger=logger)

    print()
    print("  Uninstall will remove the CineMediaVault services and application.")
    if args.remove_state:
        print("  --remove-state: the database, configuration and metadata will")
        print("  also be removed. Watch history and user accounts will be lost.")
    else:
        print("  The database, configuration and metadata will be KEPT.")
    if args.remove_generated_media:
        print("  --remove-generated-media: generated comic galleries will be removed.")
    print("  Source media (movies, TV, music, books, comics, games, recordings)")
    print("  is NEVER removed by this command.")
    print()
    if not confirm("Proceed with uninstall?", assume_yes=args.yes or ctx.dry_run):
        return 130

    result = rollback_tool.uninstall(
        ctx,
        remove_media=args.remove_generated_media,
        remove_state=args.remove_state,
        keep_backups=not args.no_keep_backups,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n  removed {len(result['removed'])} path(s)")
        if result["kept"]:
            print("  kept:")
            for item in result["kept"]:
                print(f"    {item}")
        print()
    return 0


def cmd_config(args, logger) -> int:
    if args.config_command == "example":
        print(_example_config())
        return 0
    if args.config_command == "schema":
        print(json.dumps([
            {"key": f.key, "type": f.type, "default": f.default,
             "required": f.required, "choices": list(f.choices) if f.choices else None,
             "minimum": f.minimum, "maximum": f.maximum, "secret": f.is_secret,
             "stage": f.stage, "help": f.help}
            for f in SCHEMA
        ], indent=2))
        return 0

    config = load_config(args.config)
    if args.config_command == "show":
        REDACTOR.register_config(config)
        print(loader.redacted_example(config))
        return 0
    if args.config_command == "get":
        value = get(config, args.key, None)
        from .config.schema import SCHEMA_BY_KEY
        field = SCHEMA_BY_KEY.get(args.key)
        if field is None:
            print(f"unknown key: {args.key}", file=sys.stderr)
            return 2
        if field.is_secret and value:
            print("***REDACTED***")
        else:
            print(json.dumps(value))
        return 0
    if args.config_command == "set":
        from .config.schema import SCHEMA_BY_KEY
        field = SCHEMA_BY_KEY.get(args.key)
        if field is None:
            print(f"unknown key: {args.key}", file=sys.stderr)
            return 2
        value = loader._coerce(field.type, args.value, args.key)
        put(config, args.key, value)
        problems = errors(validate(config))
        if problems:
            for issue in problems:
                print(f"  ERROR {issue.key}: {issue.message}", file=sys.stderr)
            return 2
        public, secrets = loader.split_secrets(config)
        loader.save(args.config, public, mode=0o640)
        print(f"{args.key} updated. Run `cinevaultctl apply` to regenerate "
              f"the services.")
        return 0
    return 2


def cmd_admin(args, logger) -> int:
    from .config.secrets import AdminBootstrap

    config = load_config(args.config)
    ctx = make_context(args, config, logger=logger)
    username = args.username or ctx.get("admin.username")
    if not username:
        print("no administrator username is configured", file=sys.stderr)
        return 2

    if sys.stdin.isatty():
        password = getpass.getpass(f"New password for '{username}': ")
        again = getpass.getpass("Repeat: ")
        if password != again:
            print("passwords did not match", file=sys.stderr)
            return 2
    else:
        password = sys.stdin.readline().rstrip("\n")

    problems = check_password(password)
    if problems:
        for issue in problems:
            print(f"  {issue.message}", file=sys.stderr)
        return 2

    REDACTOR.register(password)
    digest = hash_password(password)
    password = "\x00" * len(password)      # drop the plaintext immediately

    bootstrap = AdminBootstrap(ctx.layout.admin_bootstrap_file,
                               group=ctx.service_group)
    bootstrap.write(username=username, password_hash=digest,
                    full_name=ctx.get("admin.full_name") or "",
                    email=ctx.get("admin.email") or "")
    ctx.runner.systemctl("restart", "cinemediavault.service", check=False)
    print(f"password updated for '{username}'; the service was restarted")
    return 0


def cmd_epg(args, logger) -> int:
    config = load_config(args.config)
    ctx = make_context(args, config, logger=logger)
    project = ctx.layout.compose_dir / "epg" / "docker-compose.yml"

    if not project.is_file():
        print("the extended EPG collector is not installed", file=sys.stderr)
        return 2

    if args.epg_command == "rebuild":
        ctx.runner.run(["docker", "compose", "-f", str(project), "build", "--pull"],
                       timeout=1800)
        ctx.runner.run(["docker", "compose", "-f", str(project), "up", "-d"],
                       timeout=300)
        print("collector rebuilt and started")
        return 0

    status = ctx.runner.probe(
        ["docker", "inspect", "-f", "{{.State.Status}}", "cinemediavault-epg"])
    guide = ctx.layout.compose_dir / "epg" / "public" / "guide.xml"
    payload = {
        "container": status.stdout.strip() or "absent",
        "guide_present": guide.is_file(),
        "guide_bytes": guide.stat().st_size if guide.is_file() else 0,
        "guide_age_hours": (round((time.time() - guide.stat().st_mtime) / 3600, 1)
                            if guide.is_file() else None),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"\n  container:  {payload['container']}")
        print(f"  guide:      {'present' if payload['guide_present'] else 'not collected yet'}")
        if payload["guide_present"]:
            print(f"  size:       {payload['guide_bytes'] / 1024 / 1024:.1f} MB")
            print(f"  collected:  {payload['guide_age_hours']} hours ago")
        print()
    return 0


def cmd_module(args, logger) -> int:
    config = load_config(args.config)
    ctx = make_context(args, config, logger=logger)
    name = args.module_command and args.name

    script = ctx.layout.creators_dir / "games" / name / "build_library.py"
    if script.is_file():
        roms = Path(ctx.get("media.games_root") or "") / name
        ctx.runner.run(["python3", str(script), "--source", str(roms),
                        "--output", str(ctx.layout.modules_dir / name)],
                       user=ctx.service_user, timeout=3600)
        ctx.runner.systemctl("restart", f"cinemediavault-module@{name}.service",
                             check=False)
        print(f"{name} catalog rebuilt")
        return 0

    if name == "comics":
        wrapper = ctx.layout.bin_dir / "cinevault-create"
        if not wrapper.is_file():
            print("the comic creators are not installed", file=sys.stderr)
            return 2
        ctx.runner.run([str(wrapper), "comics", "new"], timeout=7200)
        ctx.runner.run([str(wrapper), "comics", "hub"], timeout=600)
        print("comic library rebuilt")
        return 0

    print(f"unknown module: {name}", file=sys.stderr)
    return 2


def cmd_transcode(args, logger) -> int:
    config = load_config(args.config)
    ctx = make_context(args, config, logger=logger)
    settings_file = ctx.layout.state_root / "transcode" / "queue-settings.json"

    if not settings_file.is_file():
        print("the bulk transcode queue is not installed", file=sys.stderr)
        return 2

    settings = json.loads(settings_file.read_text(encoding="utf-8"))

    if args.transcode_command == "status":
        print(f"\n  state:  {settings.get('state')}")
        print(f"  queued: {len(settings.get('queue') or [])}")
        print(f"  target: {settings.get('target_gb_per_hour')} GB/hour, "
              f"{settings.get('video_codec')}\n")
        return 0

    if args.transcode_command == "enable":
        print()
        print("  The bulk transcode queue REWRITES SOURCE MEDIA.")
        print("  Each title is re-encoded and the original is replaced once the")
        print("  output has been verified. Confirm you have backups.")
        print()
        if not confirm("Arm the bulk transcode queue?", assume_yes=args.yes
                       if hasattr(args, "yes") else False):
            return 130
        settings["state"] = "enabled"
    else:
        settings["state"] = "stopped"

    from .core.fsops import write_file
    write_file(settings_file, json.dumps(settings, indent=2) + "\n", mode=0o640,
               user=ctx.service_user, group=ctx.service_group)
    print(f"queue state: {settings['state']}")
    return 0


def _example_config() -> str:
    """A commented example covering every setting, with secrets masked."""
    lines = [
        "# CineMediaVault configuration example.",
        "#",
        "# Every setting is shown with its default. Delete anything you do not",
        "# need: unset keys fall back to these defaults.",
        "#",
        "# Secrets shown as ***REDACTED*** are never stored in this file when the",
        "# installer writes it; they are moved to secrets.env with mode 0640.",
        "# You MAY put real values here for an unattended install - the installer",
        "# moves them out and blanks them on the first write.",
        "",
    ]
    from .config.schema import SCHEMA
    from .core.redact import MASK

    section = ""
    for field in SCHEMA:
        head, _, _leaf = field.key.partition(".")
        if head != section:
            section = head
            lines.append("")
            lines.append(f"# {'=' * 68}")
            lines.append(f"# {head}")
            lines.append(f"# {'=' * 68}")
            lines.append(f"{head}:")
        for wrapped in _wrap(field.help, 74):
            lines.append(f"  # {wrapped}")
        if field.choices:
            lines.append(f"  # choices: {', '.join(str(c) for c in field.choices)}")
        if field.minimum is not None or field.maximum is not None:
            lines.append(f"  # range: {field.minimum} .. {field.maximum}")
        if field.required:
            lines.append("  # REQUIRED")
        value = MASK if field.is_secret and field.default in (None, "") else field.default
        rendered = loader.dump_yaml({field.key.split(".", 1)[1]: value}, indent=1)
        lines.append(rendered if rendered else
                     f"  {field.key.split('.', 1)[1]}: null")
    return "\n".join(lines) + "\n"


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    if not text:
        return []
    return textwrap.wrap(text, width=width) or []


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

COMMANDS = {
    "install": lambda a, l: cmd_install(a, l),
    "repair": lambda a, l: cmd_install(a, l, repair=True),
    "apply": lambda a, l: cmd_install(a, l, repair=True),
    "preflight": cmd_preflight,
    "plan": cmd_plan,
    "status": cmd_status,
    "smoke-test": cmd_smoke_test,
    "validate": cmd_validate,
    "backup": cmd_backup,
    "restore": cmd_restore,
    "upgrade": cmd_upgrade,
    "rollback": cmd_rollback,
    "uninstall": cmd_uninstall,
    "config": cmd_config,
    "admin": cmd_admin,
    "epg": cmd_epg,
    "module": cmd_module,
    "transcode": cmd_transcode,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    log_file = args.log_file or ""
    if not log_file and args.command in ("install", "repair", "apply", "upgrade"):
        directory = Path("/var/log/cinemediavault")
        try:
            directory.mkdir(parents=True, exist_ok=True)
            log_file = str(directory / f"install-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
        except OSError:
            log_file = ""

    logger = InstallLogger(log_file or None, level=args.log_level,
                           console=not args.quiet)
    set_logger(logger)

    try:
        handler = COMMANDS[args.command]
        return handler(args, logger)
    except InstallerError as exc:
        logger.error(str(exc))
        return exc.exit_code
    except KeyboardInterrupt:
        logger.error("interrupted")
        return 130
    except Exception as exc:                                # noqa: BLE001
        logger.error(f"unexpected error: {type(exc).__name__}: {exc}")
        if args.log_level == "debug":
            import traceback
            traceback.print_exc()
        return 1
    finally:
        logger.close()


if __name__ == "__main__":
    raise SystemExit(main())
