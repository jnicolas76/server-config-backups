"""TLS certificate provisioning.

Three supported modes:

``self_signed``  the installer generates a 10-year certificate with the
                 hostname and every local IP as subject alternative names, so
                 browsers and the Android companion reach the same certificate
                 whichever address they use.
``provided``     the operator points at an existing certificate and key. They
                 are validated and referenced in place; the private key is
                 never copied, so it never enters a CineMediaVault backup.
``acme_dns``     an existing certbot deployment is referenced in place, and a
                 renewal hook is installed so the service reloads after renewal.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..core.errors import StepError
from ..core.fsops import ensure_dir, write_file
from .base import Step, StepResult

SELF_SIGNED_DAYS = 3650


class ProvisionTLS(Step):
    id = "tls"
    title = "TLS certificate"
    description = "Generate or validate the certificate CineMediaVault serves."
    depends_on = ("network.tls_mode", "network.hostname",
                  "network.tls_cert_path", "network.tls_key_path")
    requires = ("directories",)

    def applies(self, ctx) -> bool:
        return ctx.get("network.tls_mode") != "none"

    def preview(self, ctx) -> str:
        mode = ctx.get("network.tls_mode")
        if mode == "self_signed":
            return (f"Generate a self-signed certificate for "
                    f"{ctx.get('network.hostname')} valid {SELF_SIGNED_DAYS} days.")
        if mode == "provided":
            return f"Use the certificate at {ctx.get('network.tls_cert_path')}."
        return "Reference the existing certbot certificate and add a reload hook."

    def run(self, ctx) -> StepResult:
        mode = ctx.get("network.tls_mode")
        if mode == "self_signed":
            return self._self_signed(ctx)
        if mode == "provided":
            return self._provided(ctx)
        return self._acme(ctx)

    # ------------------------------------------------------------------
    def _self_signed(self, ctx) -> StepResult:
        cert = ctx.layout.tls_dir / "cinemediavault.crt"
        key = ctx.layout.tls_dir / "cinemediavault.key"
        ensure_dir(ctx.layout.tls_dir, mode=0o750, user="root",
                   group=ctx.service_group, dry_run=ctx.dry_run, logger=ctx.logger)

        if cert.is_file() and key.is_file() and self._certificate_valid(ctx, cert):
            return StepResult(changed=False,
                              summary="existing self-signed certificate is still valid",
                              data={"cert": str(cert)})

        hostname = ctx.get("network.hostname") or "cinemediavault.local"
        names = self._subject_alt_names(ctx, hostname)
        config = self._openssl_config(hostname, names)
        config_path = ctx.layout.tls_dir / "openssl.cnf"

        if ctx.dry_run:
            ctx.logger.info(f"[dry-run] would generate a certificate for {hostname} "
                            f"with SANs: {', '.join(names)}")
            return StepResult(changed=True, summary="certificate generation planned")

        write_file(config_path, config, mode=0o640, user="root",
                   group=ctx.service_group, backups=ctx.backups, logger=ctx.logger)
        ctx.runner.run([
            "openssl", "req", "-x509", "-nodes",
            "-newkey", "rsa:4096",
            "-days", str(SELF_SIGNED_DAYS),
            "-keyout", str(key),
            "-out", str(cert),
            "-config", str(config_path),
            "-extensions", "v3_req",
        ], timeout=180)

        os.chmod(key, 0o640)
        os.chmod(cert, 0o644)
        import grp
        gid = grp.getgrnam(ctx.service_group).gr_gid
        os.chown(key, 0, gid)
        os.chown(cert, 0, gid)

        return StepResult(
            changed=True,
            summary=f"self-signed certificate for {hostname} ({len(names)} names)",
            data={"cert": str(cert), "names": names},
        ).warn(
            "Browsers will warn about a self-signed certificate the first time. "
            "Accept it once per device, or install the certificate as trusted. "
            f"The certificate is at {cert}.")

    def _provided(self, ctx) -> StepResult:
        cert = Path(ctx.get("network.tls_cert_path"))
        key = Path(ctx.get("network.tls_key_path"))
        for path, label in ((cert, "certificate"), (key, "private key")):
            if not path.is_file():
                raise StepError(f"TLS {label} not found: {path}", step=self.id,
                                recoverable=False)
        if ctx.dry_run:
            return StepResult(summary="would validate the supplied certificate")

        result = ctx.runner.run(
            ["openssl", "x509", "-noout", "-in", str(cert)], check=False)
        if result.returncode != 0:
            raise StepError(f"{cert} is not a valid PEM certificate", step=self.id,
                            recoverable=False)

        # The key must match the certificate, otherwise TLS fails at first
        # connection with an error nobody can interpret.
        cert_mod = ctx.runner.run(
            ["openssl", "x509", "-noout", "-modulus", "-in", str(cert)], check=False)
        key_mod = ctx.runner.run(
            ["openssl", "rsa", "-noout", "-modulus", "-in", str(key)], check=False)
        if cert_mod.returncode == 0 and key_mod.returncode == 0 and \
                cert_mod.stdout.strip() != key_mod.stdout.strip():
            raise StepError(
                "the supplied private key does not match the certificate",
                step=self.id, recoverable=False)

        result = StepResult(changed=False, summary=f"using {cert}")
        mode = os.stat(key).st_mode & 0o777
        if mode & 0o007:
            result.warn(f"{key} is world-readable (mode {mode:04o}). "
                        f"Run: chmod 640 {key}")
        # Readability by the service group is a hard requirement.
        import grp
        try:
            gid = grp.getgrnam(ctx.service_group).gr_gid
            if os.stat(key).st_gid != gid and not (mode & 0o004):
                result.warn(
                    f"{key} is not readable by group '{ctx.service_group}'. "
                    f"Run: chgrp {ctx.service_group} {key} && chmod 640 {key}")
        except KeyError:
            pass
        return result

    def _acme(self, ctx) -> StepResult:
        cert = Path(ctx.get("network.tls_cert_path") or "")
        if not cert.is_file():
            raise StepError(
                "acme_dns mode needs network.tls_cert_path pointing at the "
                "certbot fullchain.pem for this host",
                step=self.id, recoverable=False)
        hook = Path("/etc/letsencrypt/renewal-hooks/deploy/cinemediavault.sh")
        script = (
            "#!/bin/sh\n"
            "# Installed by CineMediaVault. Reloads the service after renewal.\n"
            "systemctl reload-or-restart cinemediavault.service || true\n"
        )
        changed = write_file(hook, script, mode=0o755, backups=ctx.backups,
                             dry_run=ctx.dry_run, logger=ctx.logger)
        return StepResult(changed=changed,
                          summary="certbot certificate referenced; renewal hook installed")

    # ------------------------------------------------------------------
    def _certificate_valid(self, ctx, cert: Path) -> bool:
        """True when the certificate parses and has more than 30 days left."""
        result = ctx.runner.probe(
            ["openssl", "x509", "-noout", "-checkend", str(30 * 86400), "-in", str(cert)])
        return result.returncode == 0

    def _subject_alt_names(self, ctx, hostname: str) -> list[str]:
        names = [hostname]
        short = hostname.split(".")[0]
        if short != hostname:
            names.append(short)
        names.extend(["localhost", "127.0.0.1", "::1"])
        from ..preflight.network import _local_addresses
        for address in sorted(_local_addresses(ctx)):
            if address not in names:
                names.append(address)
        return names

    def _openssl_config(self, hostname: str, names: list[str]) -> str:
        entries: list[str] = []
        dns_index = ip_index = 0
        for name in names:
            if _is_ip(name):
                ip_index += 1
                entries.append(f"IP.{ip_index} = {name}")
            else:
                dns_index += 1
                entries.append(f"DNS.{dns_index} = {name}")
        alt = "\n".join(entries)
        return f"""# Generated by the CineMediaVault installer.
[req]
default_bits = 4096
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req

[dn]
CN = {hostname}
O = CineMediaVault

[v3_req]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
{alt}
"""


def _is_ip(value: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def steps():
    return [ProvisionTLS()]
