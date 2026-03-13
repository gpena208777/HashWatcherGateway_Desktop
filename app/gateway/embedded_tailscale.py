#!/usr/bin/env python3
"""Embedded tailscaled manager for desktop gateway builds."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple


def _windows_subprocess_kwargs() -> dict:
    if os.name != "nt":
        return {}
    kwargs = {}
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if create_no_window:
        kwargs["creationflags"] = create_no_window
    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_cls is not None:
        startupinfo = startupinfo_cls()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _gateway_home() -> Path:
    return Path.home() / ".hashwatcher-gateway-desktop" / "tailscale"


def _platform_dir() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = machine
    return f"{system}-{arch}"


def _vendor_dir() -> Path:
    # app/gateway/embedded_tailscale.py -> repo/app/vendor/tailscale/<platform>/
    return Path(__file__).resolve().parents[1] / "vendor" / "tailscale" / _platform_dir()


def resolve_binaries() -> Tuple[Optional[str], Optional[str]]:
    """Return (tailscale_cli, tailscaled_daemon) binaries."""
    cli_override = os.getenv("TAILSCALE_BIN", "").strip()
    daemon_override = os.getenv("TAILSCALED_BIN", "").strip()
    if cli_override and daemon_override:
        return cli_override, daemon_override

    is_windows = platform.system().lower().startswith("win")
    cli_name = "tailscale.exe" if is_windows else "tailscale"
    daemon_name = "tailscaled.exe" if is_windows else "tailscaled"

    vendor = _vendor_dir()
    cli_vendor = vendor / cli_name
    daemon_vendor = vendor / daemon_name
    if cli_vendor.exists() and daemon_vendor.exists():
        return str(cli_vendor), str(daemon_vendor)

    cli = shutil.which("tailscale")
    daemon = shutil.which("tailscaled")
    return cli, daemon


def socket_path() -> Optional[str]:
    if platform.system().lower().startswith("win"):
        return None
    return str(_gateway_home() / "tailscaled.sock")


def state_path() -> str:
    if platform.system().lower().startswith("win"):
        return str(_gateway_home() / "tailscaled.state")
    return str(_gateway_home() / "tailscaled.state")


def _pid_file() -> Path:
    return _gateway_home() / "tailscaled.pid"


def _log_file() -> Path:
    return _gateway_home() / "tailscaled.log"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def is_running() -> bool:
    pid_file = _pid_file()
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    return _pid_alive(pid)


def ensure_started() -> bool:
    """Start embedded tailscaled daemon if available and not already running."""
    cli_bin, daemon_bin = resolve_binaries()
    if not cli_bin or not daemon_bin:
        return False

    # If user opts out, do not start embedded daemon.
    embedded = os.getenv("TS_EMBEDDED", "1").strip().lower() not in ("0", "false", "no")
    if not embedded:
        return False

    if is_running():
        return True

    home = _gateway_home()
    home.mkdir(parents=True, exist_ok=True)
    proc_kwargs = _windows_subprocess_kwargs()
    if platform.system().lower().startswith("win"):
        # Windows builds typically run service-based daemon. If bundled daemon exists,
        # attempt to start it in background but do not fail hard.
        try:
            proc = subprocess.Popen(
                [daemon_bin, f"--state={state_path()}"],
                stdin=subprocess.DEVNULL,
                stdout=open(_log_file(), "a", encoding="utf-8"),  # noqa: SIM115
                stderr=subprocess.STDOUT,
                **proc_kwargs,
            )
            _pid_file().write_text(str(proc.pid), encoding="utf-8")
            time.sleep(1.0)
            return _pid_alive(proc.pid)
        except Exception:
            return False

    sock = socket_path()
    if sock:
        try:
            Path(sock).unlink(missing_ok=True)
        except Exception:
            pass

    cmd = [
        daemon_bin,
        f"--state={state_path()}",
        "--tun=userspace-networking",
    ]
    if sock:
        cmd.append(f"--socket={sock}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=open(_log_file(), "a", encoding="utf-8"),  # noqa: SIM115
            stderr=subprocess.STDOUT,
            **proc_kwargs,
        )
        _pid_file().write_text(str(proc.pid), encoding="utf-8")
        time.sleep(1.25)
        return _pid_alive(proc.pid)
    except Exception:
        return False


def stop() -> None:
    pid_file = _pid_file()
    if not pid_file.exists():
        return
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        pid_file.unlink(missing_ok=True)
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass
    pid_file.unlink(missing_ok=True)
