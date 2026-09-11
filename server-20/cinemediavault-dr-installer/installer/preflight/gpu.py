"""GPU and hardware-encoder detection.

Nothing here is required: CineMediaVault plays direct and transcodes on CPU
without any GPU. The checks exist so the wizard can pick a sensible encoder,
and so an operator who selected NVENC on a machine with no NVIDIA driver finds
out before installing rather than at first playback.
"""

from __future__ import annotations

from pathlib import Path

from . import CheckResult, fail, ok, warn

ENCODER_FOR_VENDOR = {
    "nvidia": "hevc_nvenc",
    "intel": "h264_qsv",
    "amd": "h264_vaapi",
    "vaapi": "h264_vaapi",
}


def checks(ctx) -> list[CheckResult]:
    facts = ctx.facts
    results: list[CheckResult] = []
    requested = (ctx.get("transcode.hls_encoder") or "auto").lower()

    if not ctx.get("transcode.hls_enabled") and not ctx.get("subtitles.whisper_enabled"):
        results.append(ok("gpu.skip", "GPU acceleration",
                          "not required by the selected components"))
        return results

    # -- what the machine has ------------------------------------------------
    if facts.gpu_vendors:
        results.append(ok("gpu.present", "GPU",
                          ", ".join(facts.gpu_vendors),
                          vendors=list(facts.gpu_vendors)))
    else:
        results.append(warn(
            "gpu.present", "GPU", "no GPU detected.",
            "HLS transcoding will use the CPU. Expect roughly one concurrent "
            "1080p stream per two modern cores."))

    # -- render node access ----------------------------------------------------
    dri = Path("/dev/dri")
    if any(v in facts.gpu_vendors for v in ("intel", "amd", "vaapi")):
        if dri.is_dir() and any(dri.glob("renderD*")):
            results.append(ok("gpu.dri", "VA-API render node",
                              "/dev/dri/renderD* present"))
        else:
            results.append(warn(
                "gpu.dri", "VA-API render node", "/dev/dri has no render node.",
                "Install the vendor driver, and add the service user to the "
                "'render' group."))

    # -- ffmpeg encoder support -------------------------------------------------
    encoders = _ffmpeg_encoders(ctx)
    if encoders is None:
        results.append(warn(
            "gpu.ffmpeg", "ffmpeg encoders",
            "ffmpeg is not installed yet, so encoder support cannot be verified.",
            "The installer will install ffmpeg and re-check at first playback."))
    else:
        available = sorted(e for e in ENCODER_FOR_VENDOR.values() if e in encoders)
        if available:
            results.append(ok("gpu.ffmpeg", "ffmpeg encoders",
                              ", ".join(available), encoders=available))
        else:
            results.append(ok("gpu.ffmpeg", "ffmpeg encoders",
                              "software encoders only (libx264/libx265)"))

    # -- requested encoder is actually usable -------------------------------------
    if requested != "auto" and requested != "cpu":
        vendor_map = {"nvenc": "nvidia", "qsv": "intel", "vaapi": ("amd", "vaapi", "intel")}
        expected = vendor_map.get(requested)
        matched = (
            expected in facts.gpu_vendors if isinstance(expected, str)
            else any(v in facts.gpu_vendors for v in (expected or ()))
        )
        if not matched:
            results.append(fail(
                "gpu.requested", f"Requested encoder '{requested}'",
                f"no matching GPU was detected (found: "
                f"{', '.join(facts.gpu_vendors) or 'none'}).",
                "Set transcode.hls_encoder to 'auto' or 'cpu', or install the "
                "vendor driver first."))
        else:
            results.append(ok("gpu.requested", f"Requested encoder '{requested}'",
                              "supported by the detected hardware"))

    # -- Whisper sizing -------------------------------------------------------------
    if ctx.get("subtitles.whisper_enabled"):
        model = ctx.get("subtitles.whisper_model")
        device = ctx.get("subtitles.whisper_device")
        if device == "cuda" and "nvidia" not in facts.gpu_vendors:
            results.append(fail(
                "gpu.whisper", "Whisper device",
                "CUDA was requested but no NVIDIA GPU is present.",
                "Set subtitles.whisper_device to 'auto' or 'cpu'."))
        elif device in ("auto", "cpu") and model in ("medium", "large-v3"):
            results.append(warn(
                "gpu.whisper", "Whisper model",
                f"'{model}' on CPU transcribes far slower than real time.",
                "Use 'small' or 'base' on CPU-only hardware, or add an NVIDIA GPU."))
        else:
            results.append(ok("gpu.whisper", "Whisper device",
                              f"model {model} on {device}"))

    return results


def _ffmpeg_encoders(ctx) -> set[str] | None:
    ffmpeg = ctx.runner.which("ffmpeg")
    if not ffmpeg:
        return None
    result = ctx.runner.probe([ffmpeg, "-hide_banner", "-encoders"])
    if result.returncode != 0:
        return set()
    found: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith(("V", "A", "S")):
            found.add(parts[1])
    return found


def recommended_encoder(ctx) -> str:
    """Pick the encoder the wizard should default to on this hardware."""
    encoders = _ffmpeg_encoders(ctx) or set()
    for vendor in ctx.facts.gpu_vendors:
        candidate = ENCODER_FOR_VENDOR.get(vendor)
        if candidate and (not encoders or candidate in encoders):
            return {"nvidia": "nvenc", "intel": "qsv",
                    "amd": "vaapi", "vaapi": "vaapi"}[vendor]
    return "cpu"
