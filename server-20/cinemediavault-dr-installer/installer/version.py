"""Version and build metadata for the CineMediaVault installer."""

INSTALLER_NAME = "CineMediaVault Complete Installer"
INSTALLER_VERSION = "2.3.0"

# Schema version of /etc/cinemediavault/cinemediavault.yaml. The upgrade tool
# migrates older configuration files forward one schema version at a time.
CONFIG_SCHEMA_VERSION = 1

# Minimum Python the installer engine and the application both require.
MIN_PYTHON = (3, 10)

# Ubuntu releases this package is tested and supported on.
SUPPORTED_UBUNTU = ("22.04", "24.04")

# CPU architectures with a fully supported dependency set.
SUPPORTED_ARCH = ("x86_64", "aarch64")


def version_banner() -> str:
    return f"{INSTALLER_NAME} {INSTALLER_VERSION} (config schema v{CONFIG_SCHEMA_VERSION})"
