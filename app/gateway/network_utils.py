#!/usr/bin/env python3
"""Cross-platform LAN interface helpers for the desktop HashWatcher gateway."""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Iterable, Optional, Tuple

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
    if addr in ipaddress.ip_network("100.64.0.0/10"):
        return False
    return bool(addr.is_private)


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
