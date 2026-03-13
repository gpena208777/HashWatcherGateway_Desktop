#!/usr/bin/env python3
"""Tailscale helpers for the desktop HashWatcher gateway."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from . import network_utils
    from . import embedded_tailscale
except ImportError:  # pragma: no cover - direct script execution fallback
    import network_utils  # type: ignore
    import embedded_tailscale  # type: ignore


def _tailscale_bin() -> str:
    explicit = os.getenv("TAILSCALE_BIN", "").strip()
    if explicit:
        return explicit

    cli, _daemon = embedded_tailscale.resolve_binaries()
    if cli:
        return cli

    resolved = shutil.which("tailscale")
    if resolved:
        return resolved
    return "tailscale"


def _windows_subprocess_kwargs() -> Dict[str, Any]:
    if os.name != "nt":
        return {}
    kwargs: Dict[str, Any] = {}
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


def _binary_missing(returncode: int, stderr: str) -> bool:
    text = stderr.lower()
    return (
        returncode == 127
        or "not found" in text
        or "no such file or directory" in text
        or "not recognized as an internal or external command" in text
    )


def _run(cmd: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            **_windows_subprocess_kwargs(),
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, returncode=127, stdout="", stderr=f"{cmd[0]}: not found")


def _start_local_tailscale_service() -> None:
    """Best-effort launch of local Tailscale service/client."""
    if embedded_tailscale.ensure_started():
        return
    system_name = platform.system()
    proc_kwargs = _windows_subprocess_kwargs()
    try:
        if system_name == "Darwin":
            subprocess.run(
                ["open", "-a", "Tailscale"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                **proc_kwargs,
            )
            return
        if system_name == "Windows":
            service_cmds = [
                ["sc", "start", "Tailscale"],
                ["net", "start", "Tailscale"],
            ]
            for cmd in service_cmds:
                result = subprocess.run(
                    cmd,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=8,
                    **proc_kwargs,
                )
                output = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
                if result.returncode == 0:
                    return
                if "already running" in output or "already been started" in output:
                    return

            daemon_candidates = [
                r"C:\Program Files\Tailscale\tailscaled.exe",
                r"C:\Program Files (x86)\Tailscale\tailscaled.exe",
            ]
            for daemon in daemon_candidates:
                if os.path.exists(daemon):
                    subprocess.Popen(
                        [daemon],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        **proc_kwargs,
                    )
                    return
            return
    except Exception:
        return


def is_installed() -> bool:
    binary = _tailscale_bin().strip()
    if not binary:
        return False
    if os.path.isabs(binary):
        return os.path.exists(binary)
    return shutil.which(binary) is not None


def _ts_cmd(args: List[str], include_socket: bool = True) -> List[str]:
    cmd = [_tailscale_bin()]
    sock = embedded_tailscale.socket_path() if include_socket else None
    if sock:
        cmd.append(f"--socket={sock}")
    cmd.extend(args)
    return cmd


def _ensure_ip_forwarding() -> None:
    """Best-effort forwarding enablement for Linux desktop setups."""
    if os.name != "posix":
        return
    if os.uname().sysname == "Darwin":
        return
    sysctl = "/usr/sbin/sysctl" if os.path.exists("/usr/sbin/sysctl") else "sysctl"
    _run([sysctl, "-w", "net.ipv4.ip_forward=1"])
    _run([sysctl, "-w", "net.ipv6.conf.all.forwarding=1"])


def detect_subnet(interface: str = "") -> Optional[str]:
    """Auto-detect local LAN subnet (interface argument kept for compatibility)."""
    _ = interface
    return network_utils.detect_lan_subnet(host_ip=os.getenv("HOST_IP", "").strip() or None)


def _accept_routes_flag() -> str:
    raw = os.getenv("TS_ACCEPT_ROUTES", "false").strip().lower()
    enabled = raw in ("1", "true", "yes", "on")
    return "--accept-routes" if enabled else "--accept-routes=false"


def _permission_hint(stderr: str) -> str:
    text = stderr.lower()
    if (
        "permission denied" in text
        or "access is denied" in text
        or "administrator" in text
        or "must be root" in text
    ):
        return (
            "Tailscale command requires elevated privileges. "
            "Run the gateway with administrator/root permissions once to configure routes."
        )
    return ""


def _local_service_unreachable(stderr: str) -> bool:
    text = stderr.lower()
    return (
        "failed to connect to local tailscale service" in text
        or "tailscaled.sock" in text
        or "is tailscale running" in text
        or "cannot connect to local tailscaled" in text
    )


def _tailscale_up_cmd(
    *,
    hostname: str,
    advertise_routes: Optional[str] = None,
    auth_key: Optional[str] = None,
    reset: bool = False,
) -> List[str]:
    cmd = _ts_cmd(["up", f"--hostname={hostname}", _accept_routes_flag()])
    if auth_key:
        cmd.append(f"--authkey={auth_key}")
    if advertise_routes:
        cmd.append(f"--advertise-routes={advertise_routes}")
    if reset:
        cmd.append("--reset")
    return cmd


def setup(auth_key: str, subnet_cidr: Optional[str] = None) -> Dict[str, Any]:
    """Authenticate Tailscale and advertise the local subnet route."""
    if not is_installed():
        return {"ok": False, "error": "tailscale is not installed"}

    auth_key = auth_key.strip()
    if not auth_key:
        return {"ok": False, "error": "authKey is required"}
    if not auth_key.startswith("tskey-"):
        return {"ok": False, "error": "authKey must start with 'tskey-'"}

    resolved_cidr = (subnet_cidr or "").strip() or detect_subnet()
    if not resolved_cidr:
        return {
            "ok": False,
            "error": (
                "Could not detect local subnet. Enter your LAN subnet in the "
                "\"Subnet (optional)\" field (e.g. 192.168.1.0/24)."
            ),
        }

    _ensure_ip_forwarding()
    _start_local_tailscale_service()
    ts_hostname = os.getenv("PI_HOSTNAME", "HashWatcherGatewayDesktop")
    cmd = _tailscale_up_cmd(
        hostname=ts_hostname,
        advertise_routes=resolved_cidr,
        auth_key=auth_key,
        reset=True,
    )

    proc_result: Dict[str, Any] = {}

    def _run_tailscale() -> None:
        run_result = _run(cmd, timeout=120)
        proc_result["returncode"] = run_result.returncode
        proc_result["stderr"] = run_result.stderr
        proc_result["stdout"] = run_result.stdout

    thread = threading.Thread(target=_run_tailscale, daemon=True, name="tailscale-setup")
    thread.start()
    thread.join(timeout=10)

    if not thread.is_alive() and proc_result.get("returncode", 0) != 0:
        stderr = (proc_result.get("stderr") or proc_result.get("stdout") or "").strip()
        if _local_service_unreachable(stderr):
            _start_local_tailscale_service()
            time.sleep(2)
            retry = _run(cmd, timeout=120)
            if retry.returncode == 0:
                stderr = ""
            else:
                stderr = (retry.stderr or retry.stdout or "").strip()
        if not stderr:
            current = status()
            if current.get("authenticated") and current.get("ip"):
                return {
                    "ok": True,
                    "advertisedRoutes": [resolved_cidr],
                    "ip": current.get("ip"),
                    "hostname": current.get("hostname"),
                }
        hint = _permission_hint(stderr)
        error = f"tailscale up failed: {stderr}" if stderr else "tailscale up failed"
        return {"ok": False, "error": error, "hint": hint or None}

    for attempt in range(15):
        time.sleep(2 if attempt < 5 else 3)
        current = status()
        if current.get("authenticated") and current.get("ip"):
            return {
                "ok": True,
                "advertisedRoutes": [resolved_cidr],
                "ip": current.get("ip"),
                "hostname": current.get("hostname"),
            }

    current = status()
    return {
        "ok": True,
        "advertisedRoutes": [resolved_cidr],
        "ip": current.get("ip"),
        "hostname": current.get("hostname") or ts_hostname,
        "note": "Tailscale connected but IP may still be propagating.",
    }


def status() -> Dict[str, Any]:
    """Return current Tailscale status, routes, and key-expiry health."""
    info: Dict[str, Any] = {
        "installed": is_installed(),
        "running": False,
        "authenticated": False,
        "ip": None,
        "hostname": None,
        "advertisedRoutes": [],
        "online": False,
        "keyExpiry": None,
        "keyExpired": False,
        "keyExpiringSoon": False,
        "routesApproved": False,
        "routesPending": False,
    }

    if not info["installed"]:
        return info

    _start_local_tailscale_service()
    result = _run(_ts_cmd(["status", "--json"]), timeout=8)
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        if _local_service_unreachable(stderr):
            _start_local_tailscale_service()
            time.sleep(1)
            retry = _run(_ts_cmd(["status", "--json"]), timeout=6)
            result = retry
            if retry.returncode == 0:
                stderr = ""
            else:
                stderr = (retry.stderr or retry.stdout or "").strip()
        hint = _permission_hint(stderr)
        if result.returncode != 0:
            if _binary_missing(result.returncode, stderr):
                info["installed"] = False
            if hint:
                info["hint"] = hint
            return info

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return info

    backend_state = str(data.get("BackendState", ""))
    self_node = data.get("Self", {}) or {}
    auth_url = str(data.get("AuthURL") or "").strip()
    health_messages = [str(item) for item in (data.get("Health") or []) if str(item).strip()]
    health_text = " ".join(health_messages).lower()
    logged_out = (
        backend_state in {"NeedsLogin", "NoState"}
        or bool(auth_url)
        or "logged out" in health_text
        or "not logged in" in health_text
    )

    info["running"] = backend_state != "Stopped"
    tailscale_ips = self_node.get("TailscaleIPs", [])
    if tailscale_ips:
        info["ip"] = tailscale_ips[0]
    info["hostname"] = self_node.get("HostName")
    info["authenticated"] = info["running"] and not logged_out and bool(info["ip"])
    self_online = bool(self_node.get("Online")) or bool(self_node.get("Active"))
    info["online"] = info["authenticated"] and self_online

    key_expiry_raw = self_node.get("KeyExpiry")
    if key_expiry_raw:
        info["keyExpiry"] = key_expiry_raw
        try:
            expiry_dt = datetime.fromisoformat(str(key_expiry_raw).replace("Z", "+00:00"))
            now_utc = datetime.now(timezone.utc)
            info["keyExpired"] = expiry_dt <= now_utc
            info["keyExpiringSoon"] = (
                not info["keyExpired"] and (expiry_dt - now_utc).total_seconds() < 7 * 24 * 3600
            )
        except (ValueError, TypeError):
            pass

    prefs = _get_prefs()
    if prefs:
        info["advertisedRoutes"] = prefs.get("AdvertiseRoutes", []) or []

    allowed_ips = self_node.get("AllowedIPs", []) or []
    advertised = info["advertisedRoutes"]
    if advertised and info["authenticated"]:
        approved = [route for route in advertised if route in allowed_ips]
        info["routesApproved"] = len(approved) == len(advertised)
        info["routesPending"] = len(approved) < len(advertised)

    return info


def down() -> Dict[str, Any]:
    """Turn Tailscale off while keeping local auth state."""
    if not is_installed():
        return {"ok": False, "error": "tailscale is not installed"}
    result = _run(_ts_cmd(["down"]), timeout=15)
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        if "not connected" in stderr.lower() or "not running" in stderr.lower():
            return {"ok": True, "note": "Already off"}
        hint = _permission_hint(stderr)
        return {"ok": False, "error": stderr or "tailscale down failed", "hint": hint or None}
    return {"ok": True, "note": "Tailscale turned off. You can turn it back on anytime."}


def up() -> Dict[str, Any]:
    """Turn Tailscale on using existing auth state."""
    if not is_installed():
        return {"ok": False, "error": "tailscale is not installed"}

    fresh_subnet = detect_subnet()
    if fresh_subnet:
        routes_str = fresh_subnet
    else:
        prefs = _get_prefs()
        routes = prefs.get("AdvertiseRoutes", []) if prefs else []
        routes_str = ",".join(routes) if routes else ""

    _ensure_ip_forwarding()
    _start_local_tailscale_service()
    ts_hostname = os.getenv("PI_HOSTNAME", "HashWatcherGatewayDesktop")
    cmd = _tailscale_up_cmd(hostname=ts_hostname, advertise_routes=routes_str or None)
    result = _run(cmd, timeout=90)
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        if _local_service_unreachable(stderr):
            _start_local_tailscale_service()
            time.sleep(2)
            retry = _run(cmd, timeout=90)
            if retry.returncode == 0:
                result = retry
                stderr = ""
            else:
                stderr = (retry.stderr or retry.stdout or "").strip()
        if result.returncode == 0:
            current = status()
            return {
                "ok": True,
                "ip": current.get("ip"),
                "hostname": current.get("hostname"),
                "advertisedRoutes": [routes_str] if routes_str else [],
            }
        hint = _permission_hint(stderr)
        return {"ok": False, "error": stderr or "tailscale up failed", "hint": hint or None}
    current = status()
    return {
        "ok": True,
        "ip": current.get("ip"),
        "hostname": current.get("hostname"),
        "advertisedRoutes": [routes_str] if routes_str else [],
    }


def logout() -> Dict[str, Any]:
    """Disconnect and deauthorize Tailscale on this machine."""
    if not is_installed():
        # Treat as already disconnected for idempotent UX.
        return {"ok": True, "note": "Tailscale not installed; nothing to disconnect."}

    _run(_ts_cmd(["down"]), timeout=15)
    result = _run(_ts_cmd(["logout"]), timeout=20)
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        lowered = stderr.lower()
        if (
            "not logged in" in lowered
            or "already logged out" in lowered
            or "logged out" in lowered
            or "no state" in lowered
            or "no nodekey to log out" in lowered
            or _local_service_unreachable(stderr)
        ):
            return {"ok": True, "note": "Tailscale already disconnected."}
        hint = _permission_hint(stderr)
        return {"ok": False, "error": f"tailscale logout failed: {stderr}", "hint": hint or None}
    return {"ok": True}


def _get_prefs() -> Optional[Dict[str, Any]]:
    """Read tailscale debug prefs for route state."""
    result = _run(_ts_cmd(["debug", "prefs"]), timeout=15)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
