#!/usr/bin/env python3
"""Cross-platform LAN interface helpers for the desktop HashWatcher gateway."""

from __future__ import annotations

import ipaddress
import os
import platform
import socket
import subprocess
from typing import Iterable, List, Optional, Tuple

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional dependency at runtime
    psutil = None


_SKIP_IFACE_PREFIXES = (
    "lo",
    "loopback",
    "docker",
    "br-",
    "veth",
    "virbr",
    "utun",
    "tun",
    "tap",
    "tailscale",
    "wg",
    "zerotier",
    "vboxnet",
    "vmnet",
)

_PREFERRED_IFACE_PREFIXES = (
    "en",        # macOS ethernet/wifi
    "eth",       # Linux ethernet
    "wlan",      # Linux wifi
    "wi-fi",     # Windows wifi display name
    "wifi",      # Windows wifi display name
    "ethernet",  # Windows ethernet display name
)

_TAILSCALE_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def is_lan_ipv4(ip: str) -> bool:
    """Return True for private, routable LAN IPv4 addresses."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version != 4:
        return False
    if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        return False
    # Explicitly skip Tailscale's CGNAT range.
    if addr in _TAILSCALE_CGNAT_NETWORK:
        return False
    return bool(addr.is_private)


def parse_lan_routes(raw: str) -> Tuple[List[str], List[str]]:
    """Parse user-entered LAN routes into safe, canonical CIDRs.

    Users may separate routes with commas, semicolons, or new lines. Only
    private IPv4 network CIDRs are accepted: a Tailscale 100.x address, a
    public network, a host address, or overlapping routes must never be
    advertised as a subnet route.
    """
    routes: List[ipaddress.IPv4Network] = []
    errors: List[str] = []
    entries = [entry.strip() for entry in raw.replace(";", ",").replace("\n", ",").split(",")]

    for entry in entries:
        if not entry:
            continue
        try:
            network = ipaddress.ip_network(entry, strict=True)
        except ValueError:
            errors.append(f"{entry!r} is not a network CIDR (use e.g. 192.168.1.0/24)")
            continue
        if network.version != 4:
            errors.append(f"{entry!r} is not an IPv4 network")
            continue
        network_v4 = network
        if network_v4.prefixlen < 8 or network_v4.prefixlen > 30:
            errors.append(f"{entry!r} must use a prefix from /8 through /30")
            continue
        if not is_lan_ipv4(str(network_v4.network_address)):
            errors.append(f"{entry!r} is not a private LAN network")
            continue
        if any(network_v4.overlaps(existing) for existing in routes):
            errors.append(f"{entry!r} overlaps another network already listed")
            continue
        routes.append(network_v4)

    return [str(route) for route in routes], errors


def _should_skip_iface(name: str) -> bool:
    lowered = name.strip().lower()
    return any(lowered.startswith(prefix) for prefix in _SKIP_IFACE_PREFIXES)


def _iter_lan_candidates() -> Iterable[Tuple[str, str, Optional[str]]]:
    if psutil is None:
        return []
    stats = psutil.net_if_stats()
    candidates = []
    for iface, addrs in psutil.net_if_addrs().items():
        if _should_skip_iface(iface):
            continue
        iface_stats = stats.get(iface)
        if iface_stats and not iface_stats.isup:
            continue
        for entry in addrs:
            if entry.family != socket.AF_INET:
                continue
            ip = str(entry.address or "").strip()
            if not is_lan_ipv4(ip):
                continue
            candidates.append((iface, ip, str(entry.netmask or "").strip() or None))
    return candidates


def _sort_key(candidate: Tuple[str, str, Optional[str]]) -> Tuple[int, str, str]:
    iface, ip, _ = candidate
    lowered = iface.lower()
    preferred = any(lowered.startswith(prefix) for prefix in _PREFERRED_IFACE_PREFIXES)
    # Prioritize preferred interfaces first; deterministic tie-breakers after.
    return (0 if preferred else 1, lowered, ip)


def get_local_lan_ip(host_ip: Optional[str] = None) -> Optional[str]:
    """Return the best LAN IPv4 address for miner discovery and subnet scans."""
    if host_ip and is_lan_ipv4(host_ip):
        return host_ip

    candidates = list(_iter_lan_candidates())
    if candidates:
        candidates.sort(key=_sort_key)
        return candidates[0][1]

    # Platform fallbacks for environments where psutil is unavailable/incomplete.
    system_name = platform.system()
    if system_name == "Darwin":
        for iface in ("en0", "en1", "en2"):
            try:
                result = subprocess.run(
                    ["ipconfig", "getifaddr", iface],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                ip = (result.stdout or "").strip()
                if result.returncode == 0 and is_lan_ipv4(ip):
                    return ip
            except Exception:
                continue

        try:
            result = subprocess.run(
                ["ifconfig"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                for token in result.stdout.split():
                    if token.count(".") == 3 and is_lan_ipv4(token):
                        return token
        except Exception:
            pass

    if system_name == "Linux":
        try:
            result = subprocess.run(
                ["ip", "-4", "-o", "addr", "show"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                for part in result.stdout.split():
                    if "/" in part and part.count(".") == 3:
                        ip = part.split("/", 1)[0]
                        if is_lan_ipv4(ip):
                            return ip
        except Exception:
            pass

    # Last-resort fallback for restricted environments.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
        if is_lan_ipv4(ip):
            return ip
    except OSError:
        pass
    return None


def subnet_from_ipv4(ip: str, prefix: int = 24) -> Optional[str]:
    """Derive a CIDR subnet from an IPv4 address."""
    if not is_lan_ipv4(ip):
        return None
    try:
        network = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    except ValueError:
        return None
    return str(network)


def detect_lan_subnet(
    *,
    host_ip: Optional[str] = None,
    explicit_cidr: Optional[str] = None,
    default_prefix: Optional[int] = None,
) -> Optional[str]:
    """Detect a LAN subnet CIDR for Tailscale route advertisement."""
    if explicit_cidr:
        raw = explicit_cidr.strip()
        if raw:
            try:
                return str(ipaddress.ip_network(raw, strict=False))
            except ValueError:
                pass

    prefix_value = default_prefix
    if prefix_value is None:
        try:
            prefix_value = int(os.getenv("DEFAULT_LAN_PREFIX", "24"))
        except ValueError:
            prefix_value = 24
    prefix_value = max(8, min(30, prefix_value))

    ip = get_local_lan_ip(host_ip=host_ip)
    if not ip:
        return None
    return subnet_from_ipv4(ip, prefix=prefix_value)
