"""Firewall rules.

Only the ports this installation actually uses are opened, and only to the
trusted networks. Two things are deliberately never opened: the setup wizard and
the EPG collector. Both are bound to private addresses already; adding a
firewall rule for them would be the first step towards exposing them.
"""

from __future__ import annotations

from .base import Step, StepResult


class ConfigureFirewall(Step):
    id = "firewall"
    title = "Firewall"
    description = "Allow the CineMediaVault ports from the trusted networks only."
    depends_on = ("network.configure_firewall", "network.trusted_networks",
                  "network.https_port", "network.http_port", "network.enable_http",
                  "modules.bookvault_port", "modules.comics_port",
                  "modules.games", "modules.game_port_base")
    requires = ("config",)

    def applies(self, ctx) -> bool:
        return bool(ctx.get("network.configure_firewall"))

    def _rules(self, ctx) -> list[tuple[int, str]]:
        """(port, description) pairs that should be reachable from the LAN."""
        rules: list[tuple[int, str]] = []
        if ctx.get("network.tls_mode") != "none":
            rules.append((int(ctx.get("network.https_port")), "CineMediaVault HTTPS"))
        if ctx.get("network.enable_http"):
            rules.append((int(ctx.get("network.http_port")), "CineMediaVault HTTP"))
        if ctx.get("modules.bookvault"):
            rules.append((int(ctx.get("modules.bookvault_port")), "Book Vault"))
        if ctx.get("modules.comics"):
            rules.append((int(ctx.get("modules.comics_port")), "Comics"))
        base = int(ctx.get("modules.game_port_base") or 8090)
        for index, game in enumerate(ctx.selected_games()):
            rules.append((base + index, f"{game} module"))
        return rules

    def preview(self, ctx) -> str:
        rules = self._rules(ctx)
        networks = ", ".join(ctx.get("network.trusted_networks") or [])
        return (f"Allow {len(rules)} port(s) from {networks}. The setup wizard "
                f"and EPG collector are never opened.")

    def run(self, ctx) -> StepResult:
        if not ctx.runner.has("ufw"):
            return StepResult(summary="ufw is not installed").warn(
                "No firewall was configured. If this host is reachable from "
                "outside your LAN, restrict access another way.")

        rules = self._rules(ctx)
        networks = [str(n) for n in (ctx.get("network.trusted_networks") or [])]
        added = 0
        warnings: list[str] = []

        for port, description in rules:
            for network in networks:
                if ctx.dry_run:
                    ctx.logger.info(f"[dry-run] would allow {port}/tcp from {network}")
                    continue
                result = ctx.runner.run(
                    ["ufw", "allow", "from", network, "to", "any",
                     "port", str(port), "proto", "tcp",
                     "comment", f"CineMediaVault {description}"],
                    check=False, timeout=60)
                if result.returncode == 0:
                    added += 1
                else:
                    warnings.append(
                        f"could not add a rule for {port}/tcp from {network}")

        status = ctx.runner.probe(["ufw", "status"])
        inactive = "inactive" in status.stdout.lower()

        outcome = StepResult(
            changed=bool(added),
            summary=f"{added} firewall rule(s) applied for {len(rules)} port(s)",
            data={"ports": [p for p, _ in rules], "networks": networks},
        )
        if inactive:
            outcome.warn(
                "ufw is installed and the rules were added, but the firewall is "
                "not enabled. The installer does not enable it: doing so on a "
                "remote machine can cut the SSH session that is running this "
                "installer. Enable it yourself with `sudo ufw enable` after "
                "confirming your SSH rule is present.")
        for message in warnings:
            outcome.warn(message)
        return outcome


def steps():
    return [ConfigureFirewall()]
