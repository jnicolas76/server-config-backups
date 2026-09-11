"""Secret redaction.

Every log line, every progress event streamed to the wizard, and every error
message passes through :class:`Redactor`. Two independent mechanisms are used
so that a mistake in one still leaves the other:

1. **Registered values.** Anything the installer knows to be a secret (the
   admin password, API keys, webhook URLs) is registered and replaced by exact
   substring match. This catches secrets embedded in unexpected places, such as
   a URL inside a subprocess error.
2. **Pattern matching.** Regular expressions catch shapes that look like
   credentials even when the installer never saw the value - for example a
   ``password=`` assignment in third-party output, an ``Authorization: Bearer``
   header, or a private-key PEM block.

Redaction is deliberately aggressive. A false positive costs readability; a
false negative writes a credential to disk.
"""

from __future__ import annotations

import re
import threading

MASK = "***REDACTED***"

#: Values shorter than this are not worth masking by exact match: they would
#: turn ordinary words in the log into noise. Short secrets are still caught by
#: the key/value patterns below.
MIN_EXACT_LENGTH = 6

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # key = value / key: value  (json, yaml, env, query strings, CLI flags).
    # The key may carry a prefix - TMDB_API_KEY, my_client_secret - so the name
    # is matched with an optional leading word run rather than a word boundary,
    # which underscores would otherwise defeat.
    (re.compile(
        r"(?i)((?:[a-z0-9]+[_-])*"
        r"(?:api[_-]?key|apikey|access[_-]?token|read[_-]?access[_-]?token|"
        r"auth[_-]?token|bearer[_-]?token|refresh[_-]?token|client[_-]?secret|"
        r"secret[_-]?key|secret|password|passwd|pwd|passphrase|private[_-]?key|"
        r"webhook[_-]?url|credentials?)"
        r"\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&}\]]+)"),
     r"\1" + MASK),
    # --password value / --api-key=value
    (re.compile(
        r"(?i)(--(?:password|passwd|api[-_]?key|token|secret)(?:[= ]))"
        r"(\"[^\"]*\"|'[^']*'|[^\s]+)"),
     r"\1" + MASK),
    # Authorization headers
    (re.compile(r"(?i)(authorization\s*:\s*)(bearer|basic|token)?\s*\S+"),
     r"\1" + MASK),
    # Credentials embedded in a URL: scheme://user:pass@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)([^/\s:@]+):([^/\s@]+)@"),
     r"\1\2:" + MASK + "@"),
    # Webex / Slack style webhook paths
    (re.compile(r"(?i)(https?://[^\s]*?/(?:hooks|webhooks?)/)[A-Za-z0-9_\-/]{8,}"),
     r"\1" + MASK),
    # PEM private key blocks
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                re.DOTALL),
     "-----BEGIN PRIVATE KEY-----" + MASK + "-----END PRIVATE KEY-----"),
    # JWTs (TMDb v4 read access tokens are one). The signature segment is not
    # length-constrained: a truncated or example token is still a credential
    # shape that must not be echoed.
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]+"),
     MASK),
)


class Redactor:
    """Thread-safe redaction of registered values and credential-shaped text."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._values: set[str] = set()

    # -- registration ----------------------------------------------------
    def register(self, value: object) -> None:
        """Register one secret value. Short/empty values are ignored."""
        if value is None:
            return
        text = value if isinstance(value, str) else str(value)
        text = text.strip()
        if len(text) < MIN_EXACT_LENGTH:
            return
        with self._lock:
            self._values.add(text)

    def register_many(self, values) -> None:
        for value in values or ():
            self.register(value)

    def register_config(self, config: dict) -> None:
        """Register every value the schema marks as a secret."""
        from ..config.schema import SECRET_KEYS, get
        for key in SECRET_KEYS:
            self.register(get(config, key))

    def clear(self) -> None:
        with self._lock:
            self._values.clear()

    @property
    def registered_count(self) -> int:
        with self._lock:
            return len(self._values)

    # -- application ------------------------------------------------------
    def redact(self, text: object) -> str:
        if text is None:
            return ""
        out = text if isinstance(text, str) else str(text)
        with self._lock:
            # Longest first, so a secret that contains another is masked whole.
            for value in sorted(self._values, key=len, reverse=True):
                if value and value in out:
                    out = out.replace(value, MASK)
        for pattern, replacement in _PATTERNS:
            out = pattern.sub(replacement, out)
        return out

    __call__ = redact

    def redact_argv(self, argv) -> list[str]:
        """Redact a command line for display, element by element."""
        out: list[str] = []
        mask_next = False
        for arg in argv:
            text = str(arg)
            if mask_next:
                out.append(MASK)
                mask_next = False
                continue
            if re.fullmatch(r"(?i)--(password|passwd|api[-_]?key|token|secret)", text):
                out.append(text)
                mask_next = True
                continue
            out.append(self.redact(text))
        return out

    def redact_mapping(self, mapping: dict) -> dict:
        """Redact a dict for display; secret-looking keys are masked wholesale."""
        secretish = re.compile(
            r"(?i)(password|passwd|secret|token|api[_-]?key|apikey|webhook|"
            r"credential|private[_-]?key|passphrase)")
        out: dict = {}
        for key, value in (mapping or {}).items():
            if secretish.search(str(key)):
                out[key] = MASK if value not in (None, "", [], {}) else value
            elif isinstance(value, dict):
                out[key] = self.redact_mapping(value)
            elif isinstance(value, list):
                out[key] = [self.redact_mapping(v) if isinstance(v, dict)
                            else self.redact(v) if isinstance(v, str) else v
                            for v in value]
            elif isinstance(value, str):
                out[key] = self.redact(value)
            else:
                out[key] = value
        return out


#: Process-wide redactor. Registering here affects every logger and every
#: progress stream, which is exactly what we want.
REDACTOR = Redactor()


def redact(text: object) -> str:
    return REDACTOR.redact(text)
