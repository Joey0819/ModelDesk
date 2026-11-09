"""Platform detection and capability flags for cross-platform ModelDesk support.

Provides structured platform information so that macOS-specific features can
fail gracefully on Linux/Windows with clear Chinese error messages instead of
crashing or returning 500 errors.
"""
import functools
import shutil
from pathlib import Path
from platform import system as _platform_system

# ── Platform constants ───────────────────────────────────────────────
_platform = _platform_system()
is_macos: bool = _platform == "Darwin"
is_linux: bool = _platform == "Linux"
is_windows: bool = _platform == "Windows"


def get_platform_info() -> dict:
    """Return structured platform metadata."""
    return {
        "system": _platform,
        "is_macos": is_macos,
        "is_linux": is_linux,
        "is_windows": is_windows,
        "has_native_muse_bridge": is_macos,
        "tailscale_path": resolve_tailscale_path(),
    }


# ── Feature guards ───────────────────────────────────────────────────

class PlatformFeatureUnavailable(RuntimeError):
    """Raised when a macOS-only feature is invoked on an unsupported platform."""


def ensure_macos(name: str) -> None:
    """Plain guard: raise immediately when the current platform is not macOS.

    ``ensure_macos("Muse 原生桥接")``
    """
    if not is_macos:
        raise PlatformFeatureUnavailable(
            f"「{name}」仅在 macOS 上可用，当前平台（{_platform}）不支持此功能。"
        )


def macos_only(name: str):
    """Decorator: keep the function on macOS, raise on other platforms.

    ``@macos_only("Muse 原生桥接")``
    """
    message = f"「{name}」仅在 macOS 上可用，当前平台（{_platform}）不支持此功能。"

    def _decorator(func):
        import inspect

        if is_macos:
            return func
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def _async_wrapper(*args, **kwargs):
                raise PlatformFeatureUnavailable(message)
            return _async_wrapper

        @functools.wraps(func)
        def _sync_wrapper(*args, **kwargs):
            raise PlatformFeatureUnavailable(message)
        return _sync_wrapper

    return _decorator


# ── Tailscale path resolution ────────────────────────────────────────

_TAILSCALE_CANDIDATES = (
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",  # macOS GUI bundle
    "/usr/bin/tailscale",                                      # Linux distro package
    "/usr/sbin/tailscale",                                     # Linux sbin variant
    "/usr/local/bin/tailscale",                                # Homebrew / manual
    "/snap/bin/tailscale",                                     # Ubuntu snap
    "/nix/store/current-system/sw/bin/tailscale",              # NixOS
)


def resolve_tailscale_path() -> str | None:
    """Locate the Tailscale CLI binary via PATH then platform defaults."""
    which = shutil.which("tailscale")
    if which:
        return which
    for candidate in _TAILSCALE_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


# ── Muse bridge capability ───────────────────────────────────────────

has_native_muse_bridge: bool = is_macos
