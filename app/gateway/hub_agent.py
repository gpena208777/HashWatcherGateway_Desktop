#!/usr/bin/env python3
"""HashWatcher Gateway Agent.

Runs as a Docker container (Umbrel, standalone, etc.). Provides:
- Local miner polling and data normalization
- REST API for the HashWatcher iOS/macOS app
- Miner discovery via subnet scan
- Web dashboard on port 8787
- Tailscale setup and status (auth key, subnet routing, key expiry)

No BLE, no Wi-Fi provisioning, no factory reset — the host handles networking.
"""

import ipaddress
import json
import os
import platform
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse

import requests

try:
    from . import network_utils
    from . import tailscale_setup
except ImportError:  # pragma: no cover - direct script execution fallback
    import network_utils  # type: ignore
    import tailscale_setup  # type: ignore

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    psutil = None


def env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value else default


def env_int(name: str, default: int) -> int:
    try:
        return int(env_str(name, str(default)))
    except ValueError:
        return default


def pick_first(data: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


class HubState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started_at = time.time()
        self.last_poll_at_iso: Optional[str] = None
        self.last_poll_error: Optional[str] = None
        self.last_miner_data: Dict[str, Any] = {}

    def set_poll_success(self, data: Dict[str, Any]) -> None:
        with self.lock:
            self.last_poll_at_iso = now_iso()
            self.last_miner_data = data
            self.last_poll_error = None

    def set_poll_error(self, error: str) -> None:
        with self.lock:
            self.last_poll_error = error

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "startedAtIso": datetime.fromtimestamp(self.started_at, tz=timezone.utc).isoformat(),
                "uptimeSeconds": int(time.time() - self.started_at),
                "lastPollAtIso": self.last_poll_at_iso,
                "lastPollError": self.last_poll_error,
                "lastMinerData": self.last_miner_data,
            }


class HubAgent:
    def __init__(self) -> None:
        self.hostname = env_str("PI_HOSTNAME", socket.gethostname())
        self.bitaxe_host = env_str("BITAXE_HOST", "")
        self.bitaxe_scheme = env_str("BITAXE_SCHEME", "http")
        self.endpoints = self._parse_endpoints(env_str("BITAXE_ENDPOINTS", "/system/info,/api/system/info"))
        self.poll_seconds = max(5, env_int("POLL_SECONDS", 10))
        self.http_timeout_seconds = max(2, env_int("HTTP_TIMEOUT_SECONDS", 5))
        self.status_http_bind = env_str("STATUS_HTTP_BIND", "0.0.0.0")
        self.status_http_port = max(1, env_int("STATUS_HTTP_PORT", 8787))
        runtime_default = str(Path.home() / ".hashwatcher-gateway" / "runtime_config.json")
        self.runtime_config_path = env_str("RUNTIME_CONFIG_PATH", runtime_default)
        self.agent_id = env_str("AGENT_ID", "hashwatcher-gateway")

        self.paired_device_type = ""
        self.paired_miner_mac = ""
        self.paired_miner_hostname = ""
        self.paired = bool(self.bitaxe_host.strip())
        self.user_subnet_cidr = ""

        self.config_lock = threading.Lock()
        self._load_runtime_config()

        self._cpu_prev_total = 0
        self._cpu_prev_idle = 0
        total, idle = self._read_cpu_totals()
        if total is not None and idle is not None:
            self._cpu_prev_total = total
            self._cpu_prev_idle = idle

        self.session = requests.Session()
        self.state = HubState()

    @staticmethod
    def _parse_endpoints(raw: str) -> List[str]:
        endpoints: List[str] = []
        for item in raw.split(","):
            endpoint = item.strip()
            if not endpoint:
                continue
            if not endpoint.startswith("/"):
                endpoint = "/" + endpoint
            endpoints.append(endpoint)
        return endpoints

    def _load_runtime_config(self) -> None:
        if not os.path.exists(self.runtime_config_path):
            return
        try:
            with open(self.runtime_config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            bitaxe_host = str(cfg.get("bitaxeHost", "")).strip()
            endpoints = cfg.get("endpoints")
            poll_seconds = cfg.get("pollSeconds")
            paired_device_type = str(cfg.get("deviceType", "")).strip()
            paired_miner_mac = str(cfg.get("minerMac", "")).strip()
            paired_miner_hostname = str(cfg.get("minerHostname", "")).strip()
            paired_flag = cfg.get("paired")

            if bitaxe_host:
                self.bitaxe_host = bitaxe_host
            if isinstance(endpoints, list) and endpoints:
                self.endpoints = self._parse_endpoints(",".join(str(e) for e in endpoints if isinstance(e, str)))
            if isinstance(poll_seconds, int):
                self.poll_seconds = max(5, poll_seconds)
            if paired_device_type:
                self.paired_device_type = paired_device_type.lower()
            if paired_miner_mac:
                self.paired_miner_mac = paired_miner_mac.lower()
            if paired_miner_hostname:
                self.paired_miner_hostname = paired_miner_hostname
            if isinstance(paired_flag, bool):
                self.paired = paired_flag
            else:
                self.paired = bool(self.bitaxe_host.strip())
            user_subnet = str(cfg.get("userSubnetCIDR", "")).strip()
            if user_subnet:
                self.user_subnet_cidr = user_subnet
        except Exception as exc:
            print(f"[{now_iso()}] WARNING: failed to load runtime config: {exc}", flush=True)

    def _persist_runtime_config(self) -> None:
        cfg = {
            "bitaxeHost": self.bitaxe_host,
            "endpoints": self.endpoints,
            "pollSeconds": self.poll_seconds,
            "deviceType": self.paired_device_type,
            "minerMac": self.paired_miner_mac,
            "minerHostname": self.paired_miner_hostname,
            "paired": self.paired,
            "userSubnetCIDR": self.user_subnet_cidr,
            "updatedAtIso": now_iso(),
        }
        os.makedirs(os.path.dirname(self.runtime_config_path), exist_ok=True)
        with open(self.runtime_config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def get_runtime_config(self) -> Dict[str, Any]:
        with self.config_lock:
            return {
                "bitaxeHost": self.bitaxe_host,
                "endpoints": self.endpoints,
                "pollSeconds": self.poll_seconds,
                "deviceType": self.paired_device_type,
                "minerMac": self.paired_miner_mac,
                "minerHostname": self.paired_miner_hostname,
                "paired": self.paired,
            }

    def update_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self.config_lock:
            if "bitaxeHost" in payload:
                self.bitaxe_host = str(payload["bitaxeHost"]).strip()
            if "pollSeconds" in payload:
                self.poll_seconds = max(5, int(payload["pollSeconds"]))
            if "deviceType" in payload:
                self.paired_device_type = str(payload["deviceType"]).strip().lower()
            if "minerMac" in payload:
                self.paired_miner_mac = str(payload["minerMac"]).strip().lower()
            if "minerHostname" in payload:
                self.paired_miner_hostname = str(payload["minerHostname"]).strip()
            self.paired = bool(self.bitaxe_host.strip())
            self._persist_runtime_config()
            return self.get_runtime_config()

    def reset_pairing(self) -> Dict[str, Any]:
        with self.config_lock:
            self.bitaxe_host = ""
            self.paired = False
            self.paired_device_type = ""
            self.paired_miner_mac = ""
            self.paired_miner_hostname = ""
            self._persist_runtime_config()
            return self.get_runtime_config()

    # ── Miner communication ──────────────────────────────────

    def _bitaxe_url(self, host: str, endpoint: str) -> str:
        return f"{self.bitaxe_scheme}://{host}{endpoint}"

    def _parse_payload_data(self, parsed: Any) -> Dict[str, Any]:
        if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
            return parsed["data"]
        if isinstance(parsed, dict):
            return parsed
        raise ValueError(f"Unexpected JSON type: {type(parsed)}")

    def _fetch_bitaxe_from_host(self, host: str, timeout_seconds: float) -> Optional[Dict[str, Any]]:
        for endpoint in self.endpoints:
            url = self._bitaxe_url(host, endpoint)
            try:
                response = self.session.get(url, timeout=timeout_seconds)
                response.raise_for_status()
                parsed = response.json()
                data = self._parse_payload_data(parsed)
                return {"ip": host, "endpoint": endpoint, "source_url": url, "data": data}
            except Exception:
                continue
        return None

    def fetch_paired_miner(self) -> Optional[Dict[str, Any]]:
        if not self.paired or not self.bitaxe_host.strip():
            return None
        result = self._fetch_bitaxe_from_host(self.bitaxe_host, float(self.http_timeout_seconds))
        if not result:
            return None
        return {"source_url": result["source_url"], "endpoint": result["endpoint"], "data": result["data"]}

    def proxy_miner_request(self, target_ip: str, path: str, method: str = "GET", body: Optional[bytes] = None) -> Dict[str, Any]:
        if not target_ip or not target_ip.strip():
            raise ValueError("target IP is required")
        clean_path = path.strip()
        if not clean_path.startswith("/"):
            clean_path = "/" + clean_path
        url = f"{self.bitaxe_scheme}://{target_ip.strip()}{clean_path}"
        timeout = float(self.http_timeout_seconds)
        if method.upper() == "POST":
            response = self.session.post(url, data=body, timeout=timeout, headers={"Content-Type": "application/json"} if body else {})
        else:
            response = self.session.get(url, timeout=timeout)
        try:
            data = response.json()
        except Exception:
            data = response.text
        return {"ok": response.ok, "statusCode": response.status_code, "url": url, "data": data}

    def infer_device_type(self, data: Dict[str, Any], model: Any, hostname: Any) -> str:
        parts: List[str] = []
        for value in [pick_first(data, ["deviceType", "minerType"]), model, hostname]:
            if isinstance(value, str) and value.strip():
                parts.append(value.strip().lower())
        combined = " ".join(parts)
        if "bitdsk" in combined:
            return "bitdsk"
        if "octaxe" in combined or "octa" in combined:
            return "octaxe"
        if "nerdq" in combined or "qaxe" in combined:
            return "nerdq"
        return "bitaxe"

    def discover_devices(self, cidr: Optional[str] = None) -> Dict[str, Any]:
        if cidr:
            network = ipaddress.ip_network(cidr, strict=False)
        else:
            local_ip = self._get_local_ip()
            if not local_ip:
                raise RuntimeError("Unable to determine local IP for subnet scan")
            octets = local_ip.split(".")
            network = ipaddress.ip_network(f"{octets[0]}.{octets[1]}.{octets[2]}.0/24", strict=False)

        hosts = [str(ip) for ip in network.hosts()]
        if len(hosts) > 1024:
            hosts = hosts[:1024]

        found: List[Dict[str, Any]] = []
        start = time.time()

        def worker(host: str) -> Optional[Dict[str, Any]]:
            result = self._fetch_bitaxe_from_host(host, 0.8)
            if not result:
                return None
            data = result["data"]
            normalized = self.normalize(data)
            return {
                "ip": host,
                "hostname": normalized.get("hostname"),
                "mac": normalized.get("mac"),
                "model": normalized.get("model"),
                "deviceType": normalized.get("device_type"),
                "firmware": normalized.get("firmware"),
                "tempC": normalized.get("temp_c"),
                "hashrateTHS": normalized.get("hashrate_ths"),
                "powerW": normalized.get("power_w"),
                "powerEfficiencyJTH": normalized.get("power_efficiency_j_th"),
                "endpoint": result["endpoint"],
            }

        with ThreadPoolExecutor(max_workers=32) as executor:
            futures = [executor.submit(worker, host) for host in hosts]
            for future in as_completed(futures):
                try:
                    entry = future.result()
                    if entry:
                        found.append(entry)
                except Exception:
                    continue

        found.sort(key=lambda item: item.get("ip", ""))
        return {
            "ok": True,
            "scanCidr": str(network),
            "scannedHosts": len(hosts),
            "durationSeconds": round(time.time() - start, 2),
            "devices": found,
        }

    def normalize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        hashrate_ths = pick_first(data, ["hashRate", "hashRate_1m", "hashRateavg"])
        temp_c = pick_first(data, ["temp", "boardtemp", "boardTemp"])
        vr_temp_c = pick_first(data, ["vrTemp", "vrtemp"])
        power_w = pick_first(data, ["power"])
        hashrate_numeric = to_float(hashrate_ths)
        power_numeric = to_float(power_w)
        efficiency_j_th: Optional[float] = None
        if hashrate_numeric and power_numeric and hashrate_numeric > 0:
            efficiency_j_th = round(power_numeric / hashrate_numeric, 3)
        model = pick_first(data, ["deviceModel", "boardVersion", "ASICModel", "asicmodel"])
        hostname = pick_first(data, ["hostname", "hostip"])
        device_type = self.infer_device_type(data, model=model, hostname=hostname)
        return {
            "hostname": hostname, "ip": pick_first(data, ["hostip"]),
            "mac": pick_first(data, ["macAddr", "mac"]), "model": model,
            "device_type": device_type,
            "firmware": pick_first(data, ["version", "minerversion"]),
            "hashrate_ths": hashrate_ths, "temp_c": temp_c, "vr_temp_c": vr_temp_c,
            "power_w": power_w, "power_efficiency_j_th": efficiency_j_th,
            "fanspeed": pick_first(data, ["fanspeed", "fanspeedrpm", "fanrpm"]),
            "shares_accepted": pick_first(data, ["sharesAccepted"]),
            "shares_rejected": pick_first(data, ["sharesRejected"]),
            "best_diff": pick_first(data, ["bestDiff", "bestSessionDiff"]),
            "uptime_seconds": pick_first(data, ["uptimeSeconds"]),
            "stratum_url": pick_first(data, ["stratumURL"]),
            "stratum_port": pick_first(data, ["stratumPort"]),
        }

    # ── System telemetry ─────────────────────────────────────

    def _get_local_ip(self) -> Optional[str]:
        host_ip = os.getenv("HOST_IP", "").strip() or None
        return network_utils.get_local_lan_ip(host_ip=host_ip)

    def _read_cpu_totals(self):
        try:
            with open("/proc/stat", "r", encoding="utf-8") as f:
                line = f.readline().strip()
            parts = line.split()
            if len(parts) < 5 or parts[0] != "cpu":
                return None, None
            values = [int(v) for v in parts[1:]]
            return sum(values), values[3] + (values[4] if len(values) > 4 else 0)
        except Exception:
            return None, None

    def _cpu_usage_percent(self) -> Optional[float]:
        if psutil is not None:
            try:
                return round(float(psutil.cpu_percent(interval=None)), 2)
            except Exception:
                pass

        total, idle = self._read_cpu_totals()
        if total is None or idle is None:
            return None
        prev_total, prev_idle = self._cpu_prev_total, self._cpu_prev_idle
        self._cpu_prev_total, self._cpu_prev_idle = total, idle
        if prev_total <= 0 or total <= prev_total:
            return None
        total_delta = total - prev_total
        idle_delta = idle - prev_idle
        busy_delta = max(0, total_delta - idle_delta)
        return round((busy_delta / total_delta) * 100.0, 2) if total_delta > 0 else None

    def get_host_telemetry(self) -> Dict[str, Any]:
        load1, load5, load15 = None, None, None
        try:
            load = os.getloadavg()
            load1, load5, load15 = round(load[0], 3), round(load[1], 3), round(load[2], 3)
        except Exception:
            pass

        mem_total_mb, mem_used_mb, mem_pct = None, None, None

        disk_total_gb, disk_used_gb, disk_pct = None, None, None

        cpu_temp = None
        if psutil is not None:
            try:
                vm = psutil.virtual_memory()
                mem_total_mb = round(float(vm.total) / (1024 ** 2), 1)
                mem_used_mb = round(float(vm.used) / (1024 ** 2), 1)
                mem_pct = round(float(vm.percent), 1)
            except Exception:
                pass

            try:
                disk_root = Path.home().anchor or os.path.abspath(os.sep)
                du = psutil.disk_usage(disk_root)
                disk_total_gb = round(float(du.total) / (1024 ** 3), 1)
                disk_used_gb = round(float(du.used) / (1024 ** 3), 1)
                disk_pct = round(float(du.percent), 1)
            except Exception:
                pass

            try:
                temps = psutil.sensors_temperatures()  # type: ignore[attr-defined]
                for entries in temps.values():
                    if not entries:
                        continue
                    current = entries[0].current
                    if current is not None:
                        cpu_temp = round(float(current), 1)
                        break
            except Exception:
                pass
        else:
            try:
                with open("/proc/meminfo", "r", encoding="utf-8") as f:
                    lines = {l.split(":")[0]: int(l.split()[1]) for l in f if len(l.split()) >= 2}
                total = lines.get("MemTotal", 0)
                avail = lines.get("MemAvailable", 0)
                if total > 0:
                    mem_total_mb = round(total / 1024, 1)
                    mem_used_mb = round((total - avail) / 1024, 1)
                    mem_pct = round(((total - avail) / total) * 100, 1)
            except Exception:
                pass

            try:
                st = os.statvfs("/")
                total_b = st.f_frsize * st.f_blocks
                free_b = st.f_frsize * st.f_bavail
                if total_b > 0:
                    disk_total_gb = round(total_b / (1024 ** 3), 1)
                    disk_used_gb = round((total_b - free_b) / (1024 ** 3), 1)
                    disk_pct = round(((total_b - free_b) / total_b) * 100, 1)
            except Exception:
                pass

            for path in [
                "/sys/class/thermal/thermal_zone0/temp",
                "/sys/class/hwmon/hwmon0/temp1_input",
            ]:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        cpu_temp = round(int(f.read().strip()) / 1000, 1)
                    break
                except Exception:
                    continue

        return {
            "timestampIso": now_iso(),
            "hostname": socket.gethostname(),
            "localIp": self._get_local_ip(),
            "cpuPercent": self._cpu_usage_percent(),
            "cpuCount": os.cpu_count(),
            "cpuTempC": cpu_temp,
            "loadAvg1m": load1, "loadAvg5m": load5, "loadAvg15m": load15,
            "memTotalMb": mem_total_mb, "memUsedMb": mem_used_mb, "memUsedPercent": mem_pct,
            "diskTotalGb": disk_total_gb, "diskUsedGb": disk_used_gb, "diskUsedPercent": disk_pct,
            "agentUptimeSeconds": int(time.time() - self.state.started_at),
            "platform": f"hashwatcher-gateway-{platform.system().lower()}",
        }

    def get_network_info(self) -> Dict[str, Any]:
        local_ip = self._get_local_ip()
        subnet = tailscale_setup.detect_subnet()
        ts = tailscale_setup.status()

        if not subnet and self.user_subnet_cidr:
            subnet = self.user_subnet_cidr
        if not subnet:
            routes = ts.get("advertisedRoutes", [])
            if routes:
                subnet = routes[0]
        if not local_ip and subnet:
            base = subnet.split("/")[0]
            octets = base.split(".")
            if len(octets) == 4:
                local_ip = f"{octets[0]}.{octets[1]}.{octets[2]}.x (from subnet)"

        return {
            "localIp": local_ip,
            "detectedSubnet": subnet,
            "advertisedRoutes": ts.get("advertisedRoutes", []),
            "routesApproved": ts.get("routesApproved", False),
            "routesPending": ts.get("routesPending", False),
        }

    # ── HTTP server ──────────────────────────────────────────

    def start_server(self) -> None:
        agent = self

        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
                body = json.dumps(payload, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()
                self.wfile.write(body)

            def _send_html(self, body: str, status: int = 200) -> None:
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                snapshot = agent.state.snapshot()
                ts_status = tailscale_setup.status()

                network_info = agent.get_network_info()
                full_status = {
                    "ok": True,
                    "agentId": agent.agent_id,
                    "hostname": agent.hostname,
                    "bitaxeHost": agent.bitaxe_host,
                    "isPaired": agent.paired,
                    "pollSeconds": agent.poll_seconds,
                    "statusHttpPort": agent.status_http_port,
                    "pairedDeviceType": agent.paired_device_type or None,
                    "pairedMinerMac": agent.paired_miner_mac or None,
                    "pairedMinerHostname": agent.paired_miner_hostname or None,
                    "endpoints": agent.endpoints,
                    "hostTelemetry": agent.get_host_telemetry(),
                    "tailscale": ts_status,
                    "network": network_info,
                    "platform": "hashwatcher-gateway",
                    **snapshot,
                }

                if parsed.path in ["/api/status", "/healthz"]:
                    self._send_json(full_status)
                    return
                if parsed.path == "/api/config":
                    self._send_json({"ok": True, "config": agent.get_runtime_config()})
                    return
                if parsed.path == "/api/discover":
                    query = parse_qs(parsed.query)
                    cidr = (query.get("cidr") or [None])[0]
                    try:
                        self._send_json(agent.discover_devices(cidr=cidr))
                    except Exception as exc:
                        self._send_json({"ok": False, "error": str(exc)}, status=500)
                    return
                if parsed.path == "/api/tailscale/status":
                    self._send_json({"ok": True, **ts_status})
                    return
                if parsed.path == "/api/network":
                    self._send_json({"ok": True, **agent.get_network_info()})
                    return
                if parsed.path == "/api/miner/data":
                    miner = agent.fetch_paired_miner()
                    if miner:
                        normalized = agent.normalize(miner["data"])
                        self._send_json({"ok": True, "raw": miner["data"], "normalized": normalized})
                    else:
                        self._send_json({"ok": False, "error": "No paired miner or miner unreachable"}, status=404)
                    return
                if parsed.path in ["/", "/index.html"]:
                    self._send_html(agent._build_dashboard_html(full_status, ts_status, network_info))
                    return
                if parsed.path.endswith(".png") and "/" not in parsed.path.lstrip("/"):
                    filename = parsed.path.lstrip("/")
                    base_dir = os.path.dirname(os.path.abspath(__file__))
                    img_path = os.path.join(base_dir, "assets", filename)
                    if not os.path.exists(img_path):
                        img_path = os.path.join(base_dir, filename)
                    try:
                        with open(img_path, "rb") as f:
                            data = f.read()
                        self.send_response(200)
                        self.send_header("Content-Type", "image/png")
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("Cache-Control", "public, max-age=86400")
                        self.end_headers()
                        self.wfile.write(data)
                    except FileNotFoundError:
                        self._send_json({"error": "Not found"}, status=404)
                    return

                self._send_json({"error": "Not found"}, status=404)

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                allowed = [
                    "/api/config", "/api/reset", "/api/miner/proxy",
                    "/api/tailscale/setup", "/api/tailscale/logout",
                    "/api/tailscale/down", "/api/tailscale/up",
                ]
                if parsed.path not in allowed:
                    self._send_json({"error": "Not found"}, status=404)
                    return
                content_length = int(self.headers.get("Content-Length", 0))
                raw_body = self.rfile.read(content_length) if content_length > 0 else b""
                try:
                    payload = json.loads(raw_body) if raw_body else {}
                except json.JSONDecodeError:
                    self._send_json({"error": "Invalid JSON"}, status=400)
                    return

                if parsed.path == "/api/config":
                    config = agent.update_config(payload)
                    self._send_json({"ok": True, "config": config})
                    return
                if parsed.path == "/api/reset":
                    config = agent.reset_pairing()
                    self._send_json({"ok": True, "config": config})
                    return
                if parsed.path == "/api/tailscale/setup":
                    auth_key = str(payload.get("authKey") or "")
                    subnet_cidr = str(payload.get("subnetCIDR") or "")
                    result = tailscale_setup.setup(auth_key=auth_key, subnet_cidr=subnet_cidr or None)
                    if result.get("ok") and subnet_cidr.strip():
                        with agent.config_lock:
                            agent.user_subnet_cidr = subnet_cidr.strip()
                            agent._persist_runtime_config()
                    http_status = 200 if result.get("ok") else 400
                    self._send_json(result, status=http_status)
                    return
                if parsed.path == "/api/tailscale/logout":
                    result = tailscale_setup.logout()
                    http_status = 200 if result.get("ok") else 400
                    self._send_json(result, status=http_status)
                    return
                if parsed.path == "/api/tailscale/down":
                    result = tailscale_setup.down()
                    http_status = 200 if result.get("ok") else 400
                    self._send_json(result, status=http_status)
                    return
                if parsed.path == "/api/tailscale/up":
                    result = tailscale_setup.up()
                    http_status = 200 if result.get("ok") else 400
                    self._send_json(result, status=http_status)
                    return
                if parsed.path == "/api/miner/proxy":
                    target_ip = str(payload.get("ip") or "")
                    target_path = str(payload.get("path") or "/api/system/info")
                    method = str(payload.get("method") or "GET").upper()
                    proxy_body = payload.get("body")
                    body_bytes = json.dumps(proxy_body).encode() if proxy_body else None
                    try:
                        result = agent.proxy_miner_request(target_ip, target_path, method, body_bytes)
                        self._send_json(result)
                    except Exception as exc:
                        self._send_json({"ok": False, "error": str(exc)}, status=500)
                    return

        server = ThreadingHTTPServer((agent.status_http_bind, agent.status_http_port), Handler)
        print(f"[{now_iso()}] HTTP server on {agent.status_http_bind}:{agent.status_http_port}", flush=True)
        server.serve_forever()

    @staticmethod
    def _format_uptime(seconds) -> str:
        if seconds is None:
            return "-"
        s = int(seconds)
        days, s = divmod(s, 86400)
        hours, s = divmod(s, 3600)
        mins, _ = divmod(s, 60)
        if days > 0:
            return f"{days}d {hours}h {mins}m"
        if hours > 0:
            return f"{hours}h {mins}m"
        return f"{mins}m"

    @staticmethod
    def _format_mem(mb) -> str:
        if mb is None:
            return "-"
        if mb >= 1024:
            return f"{round(mb / 1024, 1)} GB"
        return f"{round(mb)} MB"

    def _build_dashboard_html(self, status: Dict[str, Any], ts_status: Dict[str, Any], network_info: Optional[Dict[str, Any]] = None) -> str:
        host = status.get("hostTelemetry") or {}
        net = network_info or {}
        detected_subnet = net.get("detectedSubnet") or "-"
        local_ip = net.get("localIp") or "-"
        ts_ip = ts_status.get("ip", "-")
        ts_hostname_actual = ts_status.get("hostname", "-")
        ts_machine_name = os.getenv("PI_HOSTNAME", "HashWatcherGateway")
        ts_online = ts_status.get("online", False)
        ts_installed = ts_status.get("installed", False)
        key_expired = ts_status.get("keyExpired", False)
        key_expiring = ts_status.get("keyExpiringSoon", False)
        routes_pending = ts_status.get("routesPending", False)
        routes_approved = ts_status.get("routesApproved", False)
        all_done = ts_online and routes_approved and not routes_pending
        can_turn_on = bool(ts_status.get("advertisedRoutes")) or ts_status.get("authenticated", False)
        key_expiry_raw = ts_status.get("keyExpiry", "")
        app_version = env_str("APP_VERSION", "latest")
        support_mailto = (
            "mailto:info@engineeredessentials.com"
            "?subject=HashWatcherGateway%20help"
            f"&body=%0A%0A---%0AApp%20version:%20{app_version}%0A"
        )

        expiry_banner = ""
        if key_expired:
            expiry_banner = '<div class="alert alert-red">&#9888; <strong>Tailscale key expired!</strong> Remote access is unavailable. <a href="https://login.tailscale.com/admin/machines" target="_blank">Reauthorize on the Machines page &rarr;</a></div>'
        elif key_expiring:
            expiry_banner = f'<div class="alert alert-yellow">&#9888; <strong>Tailscale key expiring soon</strong> (expires {key_expiry_raw or "within 7 days"}). <a href="https://login.tailscale.com/admin/machines" target="_blank">Disable key expiry &rarr;</a></div>'

        ts_status_class = "badge-green" if ts_online else "badge-red"
        ts_status_label = "Connected" if ts_online else ("Not Installed" if not ts_installed else "Offline")

        return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#000000"><link rel="icon" type="image/png" href="/icon.png">
<title>HashWatcher Gateway</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', sans-serif; background: #000000; color: #ffffff; margin: 0; padding: 16px; line-height: 1.5; min-height: 100vh; overflow-x: hidden; }}
  .bg-canvas {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 0; pointer-events: none; overflow: hidden; }}
  .bg-orb {{ position: absolute; border-radius: 50%; filter: blur(80px); opacity: 0.35; animation: drift 20s ease-in-out infinite alternate; }}
  .bg-orb:nth-child(1) {{ width: 400px; height: 400px; background: #00cc66; top: -10%; left: -10%; animation-duration: 22s; }}
  .bg-orb:nth-child(2) {{ width: 350px; height: 350px; background: #33e680; top: 40%; right: -15%; animation-duration: 18s; animation-delay: -5s; }}
  .bg-orb:nth-child(3) {{ width: 300px; height: 300px; background: #004d26; bottom: -5%; left: 20%; animation-duration: 25s; animation-delay: -10s; }}
  @keyframes drift {{
    0% {{ transform: translate(0, 0) scale(1); }}
    33% {{ transform: translate(30px, -40px) scale(1.05); }}
    66% {{ transform: translate(-20px, 20px) scale(0.95); }}
    100% {{ transform: translate(10px, -10px) scale(1.02); }}
  }}
  .container {{ max-width: 640px; margin: 0 auto; position: relative; z-index: 1; }}
  .card {{ background: rgba(28,28,30,0.85); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.12); border-radius: 14px; padding: 20px; margin-bottom: 16px; }}
  h2 {{ margin: 0 0 12px; color: #ffffff; font-size: 1.4em; font-weight: 700; }}
  h3 {{ margin: 16px 0 8px; color: #ffffff; font-size: 1.1em; font-weight: 600; }}
  .grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }}
  .grid > div {{ background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; padding: 10px 12px; font-size: 0.9em; color: rgba(255,255,255,0.55); }}
  .grid > div strong {{ color: #ffffff; }}
  .muted {{ color: rgba(255,255,255,0.55); }}
  code {{ color: #00cc66; background: rgba(0,204,102,0.1); padding: 1px 5px; border-radius: 3px; font-size: 0.9em; }}
  a {{ color: #33e680; text-decoration: none; }}
  a:hover {{ text-decoration: underline; color: #66ff99; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; font-weight: 600; }}
  .badge-green {{ background: rgba(0,204,102,0.15); color: #33e680; }}
  .badge-red {{ background: rgba(239,68,68,0.15); color: #fca5a5; }}
  .badge-yellow {{ background: rgba(245,158,11,0.15); color: #fde68a; }}
  .alert {{ padding: 12px 16px; border-radius: 12px; margin: 12px 0; font-size: 0.9em; }}
  .alert-red {{ background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.3); color: #fca5a5; }}
  .alert-red a {{ color: #fca5a5; text-decoration: underline; }}
  .alert-yellow {{ background: rgba(245,158,11,0.1); border: 1px solid rgba(245,158,11,0.3); color: #fde68a; }}
  .alert-yellow a {{ color: #fde68a; text-decoration: underline; }}
  .alert-blue {{ background: rgba(0,204,102,0.08); border: 1px solid rgba(0,204,102,0.25); color: #33e680; }}
  .alert-blue a {{ color: #33e680; text-decoration: underline; }}
  .step {{ background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 14px 16px; margin: 10px 0; }}
  .step-num {{ display: inline-block; background: #00cc66; color: #000; width: 24px; height: 24px; border-radius: 50%; text-align: center; line-height: 24px; font-size: 0.8em; font-weight: 700; margin-right: 8px; vertical-align: middle; }}
  .step-done {{ background: #fff; color: #00cc66 !important; width: 28px; height: 28px; line-height: 28px; font-size: 1.1em; box-shadow: 0 0 8px rgba(0,204,102,0.4); }}
  .step-completed {{ background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.05); }}
  .step-completed strong {{ color: #4b5563 !important; }}
  .step-completed p,
  .step-completed a,
  .step-completed li,
  .step-completed ol,
  .step-completed summary,
  .step-completed code,
  .step-completed em,
  .step-completed span:not(.step-num),
  .step-completed details,
  .step-completed details p,
  .step-completed div {{ color: #4b5563 !important; }}
  .step-completed a {{ color: #4b5563 !important; }}
  .step-completed a[href] {{ pointer-events: auto !important; color: #33e680 !important; }}
  .step-completed a[href].btn {{ background: #00cc66 !important; color: #000 !important; }}
  .step-completed .btn:not(a) {{ background: #2c2c2e !important; color: #4b5563 !important; pointer-events: none; cursor: default; }}
  .step-completed input {{ border-color: rgba(255,255,255,0.08) !important; color: #4b5563 !important; background: rgba(255,255,255,0.03) !important; pointer-events: none; }}
  .step-completed button {{ background: #2c2c2e !important; color: #4b5563 !important; pointer-events: none; cursor: default; border-color: rgba(255,255,255,0.08) !important; }}
  .step-action {{ background: #f59e0b; animation: pulse 1.5s ease-in-out infinite; }}
  @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.5; }} }}
  .step-highlight {{ border: 2px solid #f59e0b; background: rgba(245,158,11,0.08); }}
  .divider {{ border: none; border-top: 1px solid rgba(255,255,255,0.1); margin: 16px 0; }}
  .btn {{ display: inline-block; background: #00cc66; color: #000; padding: 8px 16px; border-radius: 10px; font-weight: 600; font-size: 0.9em; text-decoration: none; border: none; cursor: pointer; }}
  .btn:hover {{ background: #33e680; text-decoration: none; }}
  .btn:disabled {{ opacity: 0.5; cursor: not-allowed; }}
  ul {{ padding-left: 20px; margin: 6px 0; }}
  li {{ margin: 4px 0; }}
  .info-row {{ display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }}
  .info-row:last-child {{ border-bottom: none; }}
  .info-label {{ color: rgba(255,255,255,0.5); }}
  .collapsible {{ cursor: pointer; user-select: none; }}
  .collapsible:hover {{ color: #33e680; }}
  details summary {{ cursor: pointer; color: #33e680; font-weight: 600; padding: 4px 0; }}
  #setupGuide > summary {{ list-style: none; }}
  #setupGuide > summary::marker {{ display: none; content: ''; }}
  #setupGuide > summary::-webkit-details-marker {{ display: none; }}
  details summary:hover {{ color: #66ff99; }}
  details[open] summary {{ margin-bottom: 8px; }}
  .brand-header {{ display: flex; align-items: center; justify-content: center; gap: 12px; padding: 20px 0 8px; }}
  .brand-header img {{ width: 48px; height: 48px; border-radius: 12px; }}
  .brand-header span {{ font-size: 1.3em; font-weight: 700; color: #fff; letter-spacing: -0.02em; }}
</style>
</head><body>
<div class="bg-canvas">
  <div class="bg-orb"></div>
  <div class="bg-orb"></div>
  <div class="bg-orb"></div>
</div>
<div class="container">

  <div class="brand-header">
    <img src="/icon.png" alt="HashWatcher">
    <span>HashWatcher Gateway</span>
    <a href="https://x.com/HashWatcher" target="_blank" title="Follow @HashWatcher on X" style="margin-left:4px;display:inline-flex;align-items:center;color:rgba(255,255,255,0.6);transition:color 0.2s;">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>
    </a>
  </div>

  <!-- Status Card -->
  <div class="card">
    <h2>Status <span id="statusBadge" class="badge {ts_status_class}">{ts_status_label}</span></h2>
    <div class="info-row"><span class="info-label">Local IP</span><code id="statusLocalIp">{local_ip}</code></div>
    <div class="info-row"><span class="info-label">Local Network</span><code id="statusLocalNetwork">{detected_subnet}</code></div>
    <div class="info-row"><span class="info-label">Tailscale IP</span><code id="statusTailscaleIp">{ts_ip}</code></div>
    <div class="info-row"><span class="info-label">Tailscale Hostname</span><code id="statusTailscaleHostname">{ts_hostname_actual}</code></div>
    {expiry_banner}
    <hr class="divider">
    <h3 style="margin:8px 0 10px;">System</h3>
    <div class="grid">
      <div>CPU: <strong>{host.get('cpuPercent', '-')}%</strong> ({host.get('cpuCount', '-')} cores)</div>
      <div>CPU Temp: <strong>{str(int(host['cpuTempC'])) + ' °C / ' + str(int(host['cpuTempC'] * 9/5 + 32)) + ' °F' if host.get('cpuTempC') is not None else '-'}</strong></div>
      <div>Load: <strong>{host.get('loadAvg1m', '-')}</strong> / {host.get('loadAvg5m', '-')} / {host.get('loadAvg15m', '-')}</div>
      <div>Memory: <strong>{self._format_mem(host.get('memUsedMb'))} / {self._format_mem(host.get('memTotalMb'))}</strong> ({host.get('memUsedPercent', '-')}%)</div>
      <div>Disk: <strong>{host.get('diskUsedGb', '-')} / {host.get('diskTotalGb', '-')} GB</strong> ({host.get('diskUsedPercent', '-')}%)</div>
      <div>Uptime: <strong>{self._format_uptime(host.get('agentUptimeSeconds'))}</strong></div>
    </div>
  </div>

  <!-- Tailscale Controls -->
  <div class="card" id="tsControlCard" style="{'border:1px solid rgba(245,158,11,0.4);' if ts_online and not routes_approved else ''}">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;">
      <div>
        <h2 style="margin:0;">Tailscale <span id="tsBadge" class="badge {'badge-yellow' if ts_online and routes_pending else ts_status_class}">{'Routes Pending' if ts_online and routes_pending else ts_status_label}</span></h2>
        <p id="tsSubtitle" class="muted" style="margin:4px 0 0;font-size:0.85em;">{'IP: <code>' + str(ts_ip) + '</code> &middot; Subnet: <code>' + str(detected_subnet) + '</code>' if ts_online else 'Remote access is off. ' + ('Use the button to reconnect.' if can_turn_on else 'Complete the setup below to connect.')}</p>
      </div>
      <div style="display:flex;gap:8px;align-items:center;">
        {'<button onclick="turnOffTailscale()" class="btn" style="background:#2c2c2e;color:rgba(255,255,255,0.7);border:1px solid rgba(255,255,255,0.15);">Turn Off</button><a href="#" onclick="disconnectTailscale(); return false;" style="color:rgba(255,255,255,0.35);font-size:0.8em;margin-left:4px;">Disconnect</a>' if ts_online else '<button onclick="turnOnTailscale()" class="btn">Turn On</button>' if can_turn_on else ''}
      </div>
    </div>
    <div id="tsStep4Warning" class="alert alert-yellow" style="margin:10px 0 0; {'display:block;' if ts_online and not routes_approved else 'display:none;'}">&#9888; <strong>Step 4 not complete yet.</strong> Waiting for API confirmation that subnet routes are approved. Go to the <a href="https://login.tailscale.com/admin/machines" target="_blank">Tailscale Machines page</a>, find <code>{ts_machine_name}</code>, click <strong>&hellip;</strong> &rarr; <strong>Edit route settings</strong>, and approve <code>{detected_subnet}</code>. This warning clears only when the API reports routes approved.</div>
    {expiry_banner}
    <span id="tsControlResult" style="font-size:0.85em;display:block;margin-top:6px;"></span>
  </div>

  <!-- Setup Guide -->
  <div class="card">
    <details id="setupGuide" open>
      <summary style="cursor:pointer;list-style:none;display:flex;align-items:center;justify-content:space-between;gap:12px;">
        <h2 style="margin:0;" id="setupGuideTitle">Setup Guide</h2>
        <span class="muted" style="font-size:0.85em;" id="setupGuideTap"></span>
      </summary>
    {'<div class="alert alert-blue" style="margin-top:12px;">&#9989; <strong>Tailscale is connected.</strong> IP: <code>' + str(ts_ip) + '</code></div>' if ts_online else ''}

    <div class="step" id="step1">
      <span class="step-num" id="step1badge">1</span>
      <strong>Download the HashWatcher App</strong>
      <p class="muted" style="margin:6px 0 0;">Get the free companion app to monitor your miners from anywhere.</p>
      <p style="margin:8px 0 0;"><a class="btn" href="https://www.HashWatcher.app" target="_blank" onclick="markStep1Done()">Download at HashWatcher.app</a></p>
      <p class="muted" style="font-size:0.85em; margin-top:8px;">Available for iOS, Mac and Android.</p>
    </div>

    <div class="step {'step-completed' if ts_online else ''}" id="step2">
      <span class="step-num {'step-done' if ts_online else ''}" id="step2badge">{'&#10003;' if ts_online else '2'}</span>
      <strong>Get a Tailscale Auth Key</strong>
      <p class="muted" style="margin:6px 0 0;">
        Go to the <a href="https://login.tailscale.com/admin/settings/keys" target="_blank">Tailscale Keys page</a> and generate an auth key.
        If you don&rsquo;t have a Tailscale account, <a href="https://tailscale.com" target="_blank">sign up free</a> first.
      </p>
      <details style="margin-top:8px;">
        <summary style="cursor:pointer;color:#33e680;font-size:0.9em;">What is an auth key?</summary>
        <p class="muted" style="font-size:0.85em; margin:6px 0 0;">
          Auth keys let you register devices to your Tailscale network without a browser login &mdash; perfect for servers and IoT devices like this gateway.
          The key authenticates this device as <em>you</em> on your private Tailscale network (tailnet), giving it secure access to your other devices.
        </p>
      </details>
      {'<div style="margin:10px 0 0;display:flex;flex-direction:column;gap:8px;max-width:260px;"><a class="btn" href="https://login.tailscale.com/admin/settings/keys" target="_blank" style="text-align:center;">Open Tailscale Keys Page</a><button class="btn" onclick="markStep2Done()" id="step2DoneBtn" style="background:#00cc66;text-align:center;">I Have My Key &rarr;</button></div>' if not ts_online else ''}
    </div>

    <div class="step {'step-completed' if ts_online else ''}">
      <span class="step-num {'step-done' if ts_online else ''}">{'&#10003;' if ts_online else '3'}</span>
      <strong>Enter Your Auth Key</strong>
      <div style="margin:8px 0;">
        <input type="text" id="tsAuthKey" placeholder="tskey-auth-..." style="width:100%;padding:8px 12px;border-radius:10px;border:1px solid rgba(255,255,255,0.15);background:rgba(255,255,255,0.05);color:#fff;font-family:monospace;font-size:0.9em;">
      </div>
      <div style="margin:12px 0 8px;">
        <label for="tsSubnetCIDR" style="display:block;font-size:0.9em;margin-bottom:4px;">Subnet (optional)</label>
        <input type="text" id="tsSubnetCIDR" placeholder="e.g. 192.168.1.0/24 or 10.51.127.0/24" style="width:100%;padding:8px 12px;border-radius:10px;border:1px solid rgba(255,255,255,0.15);background:rgba(255,255,255,0.05);color:#fff;font-family:monospace;font-size:0.9em;">
        <p class="muted" style="font-size:0.8em;margin:4px 0 0;">If auto-detect fails, type your LAN subnet here. Use the first three numbers of your network plus <code>.0/24</code> (e.g. <code>192.168.1.0/24</code> or <code>10.51.127.0/24</code>).</p>
      </div>
      <button onclick="connectTailscale()" class="btn" id="tsConnectBtn">Connect Tailscale</button>
      <span id="tsResult" style="margin-left:10px;font-size:0.9em;"></span>
    </div>

    <div id="step4Row" class="step {'step-highlight' if ts_online and not routes_approved else 'step-completed' if routes_approved else ''}">
      <span class="step-num {'step-action' if ts_online and not routes_approved else 'step-done' if routes_approved else ''}" id="step4badge">{'&#10003;' if routes_approved else '4'}</span>
      <strong>Approve Subnet Routes</strong>
      <div id="step4InlineWarning" class="alert alert-yellow" style="margin:8px 0; {'display:block;' if ts_online and not routes_approved else 'display:none;'}">&#9888; <strong>Step 4 not complete yet:</strong> routes are not approved in API status. Approve subnet routes in the Tailscale admin console and wait for API confirmation.</div>
      <p class="muted" style="margin:6px 0 0;">Go to the <a href="https://login.tailscale.com/admin/machines" target="_blank">Tailscale Machines page</a>. Find <code>{ts_machine_name}</code>, click the <strong>&hellip;</strong> menu, then <strong>Edit route settings</strong>. Approve the route for your local network <code>{detected_subnet}</code>.</p>
      <details style="margin-top:10px;">
        <summary style="cursor:pointer;color:#33e680;font-size:0.9em;">Show me how</summary>
        <p class="muted" style="font-size:0.85em;margin:8px 0 4px;">1. Find your device and click the <strong>&hellip;</strong> menu:</p>
        <img src="/step4a.png" alt="Find device on Machines page" style="width:100%;border-radius:8px;border:1px solid rgba(255,255,255,0.1);margin-bottom:10px;">
        <p class="muted" style="font-size:0.85em;margin:0 0 4px;">2. Select <strong>Edit route settings&hellip;</strong> from the menu:</p>
        <img src="/step4b.png" alt="Click Edit route settings" style="width:100%;max-width:320px;border-radius:8px;border:1px solid rgba(255,255,255,0.1);margin-bottom:10px;">
        <p class="muted" style="font-size:0.85em;margin:0 0 4px;">3. Check the subnet route, then click <strong>Save</strong>:</p>
        <img src="/step4c.png" alt="Check subnet and click Save" style="width:100%;max-width:400px;border-radius:8px;border:1px solid rgba(255,255,255,0.1);">
      </details>
    </div>

    <div class="step" id="step5">
      <span class="step-num" id="step5badge">5</span>
      <strong>Install Tailscale on Your Phone</strong>
      <p class="muted" style="margin:6px 0 0;">Download the official <a href="https://tailscale.com/download" target="_blank">Tailscale app</a> on your iPhone or Android and sign in with the same account. Your phone and this gateway are now on the same private network.</p>
      <p style="margin:10px 0 0;"><a class="btn" href="https://tailscale.com/download" target="_blank" style="margin-right:8px;">Download Tailscale</a><button class="btn" onclick="markStep5Done()" id="step5DoneBtn" style="background:#00cc66;">Done &check;</button></p>
    </div>

    <div class="step" id="step6">
      <span class="step-num" id="step6badge">6</span>
      <strong>Disable Key Expiry (Recommended)</strong>
      <p class="muted" style="margin:6px 0 0;">Your gateway is an always-on device, so you should disable key expiry to prevent your remote connection from dropping unexpectedly.</p>
      <div style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:12px 16px;margin:10px 0;">
        <p style="margin:0 0 8px;font-size:0.9em;color:#fff;font-weight:600;">How to disable key expiry:</p>
        <ol style="margin:0;padding-left:20px;font-size:0.9em;">
          <li style="margin:4px 0;">Open the <a href="https://login.tailscale.com/admin/machines" target="_blank">Tailscale Machines page</a></li>
          <li style="margin:4px 0;">Find your device (look for <code>{ts_machine_name}</code>)</li>
          <li style="margin:4px 0;">Click the <strong>&hellip;</strong> menu on the right side of the row</li>
          <li style="margin:4px 0;">Toggle <strong>Disable key expiry</strong> and save</li>
        </ol>
      </div>
      <details style="margin-top:8px;">
        <summary style="cursor:pointer;color:#33e680;font-size:0.9em;">Why disable key expiry?</summary>
        <p class="muted" style="font-size:0.85em; margin:6px 0 0;">
          Each device on your Tailscale network has a <em>node key</em> that expires every 180 days by default.
          When it expires, the device goes offline and you lose remote access until you re-authenticate.
          For always-on devices like this gateway, disabling key expiry means it stays connected permanently &mdash; no maintenance needed.
          The auth key you used to set up the device is separate; even if that auth key expires, your device stays authorized until the node key expires.
          You can also change the default expiry period from the <a href="https://login.tailscale.com/admin/settings/device-management" target="_blank">Device Management page</a>.
        </p>
      </details>
    </div>

    <div style="text-align:center;margin:18px 0 6px;">
      <button id="setupCompleteBtn" class="btn" onclick="markSetupComplete()" style="background:#00cc66;color:#000;font-size:1em;padding:12px 32px;font-weight:700;border-radius:12px;">Setup Complete &#10003;</button>
    </div>
    <p class="muted" style="font-size:0.85em;text-align:center;margin:8px 0 0;">
      <a href="{support_mailto}" style="text-decoration:underline;">Questions?</a>
    </p>

    </details>
  </div>

  <!-- Key Status -->
  <div class="card">
    <h2>&#128272; Key Status</h2>
    {expiry_banner if expiry_banner else '<div class="alert alert-blue">&#9989; Your Tailscale key is healthy. No action needed.</div>' if ts_online else '<div class="alert alert-blue">Tailscale is not connected. Complete the setup above to enable remote access.</div>'}
    <p class="muted" style="font-size:0.85em; margin-top:8px;">The HashWatcher app will alert you if the key is about to expire. <a href="https://tailscale.com/docs/features/access-control/auth-keys" target="_blank">Learn more about auth keys &rarr;</a></p>
  </div>


</div>

<script>
function markStepDone(stepId, badgeId, btnId, storageKey) {{
  const step = document.getElementById(stepId);
  const badge = document.getElementById(badgeId);
  if (step) step.classList.add('step-completed');
  if (badge) {{ badge.classList.add('step-done'); badge.innerHTML = '&#10003;'; }}
  const btn = document.getElementById(btnId);
  if (btn) btn.style.display = 'none';
  if (storageKey) try {{ localStorage.setItem(storageKey, '1'); }} catch (e) {{}}
}}
function restoreStepDone(stepId, badgeId, btnId, storageKey) {{
  if (!storageKey) return;
  try {{
    if (localStorage.getItem(storageKey) !== '1') return;
  }} catch (e) {{ return; }}
  const step = document.getElementById(stepId);
  const badge = document.getElementById(badgeId);
  const btn = document.getElementById(btnId);
  if (step) step.classList.add('step-completed');
  if (badge) {{ badge.classList.add('step-done'); badge.innerHTML = '&#10003;'; }}
  if (btn) btn.style.display = 'none';
}}
function markStep1Done() {{
  markStepDone('step1', 'step1badge', 'step1DoneBtn', 'hw_step1_done');
}}
function markStep2Done() {{
  markStepDone('step2', 'step2badge', 'step2DoneBtn', null);
  const input = document.getElementById('tsAuthKey');
  if (input) {{ input.focus(); }}
}}
function markStep5Done() {{
  markStepDone('step5', 'step5badge', 'step5DoneBtn', 'hw_step5_done');
}}
async function connectTailscale() {{
  const key = document.getElementById('tsAuthKey').value.trim();
  const subnetEl = document.getElementById('tsSubnetCIDR');
  const subnet = subnetEl ? subnetEl.value.trim() : '';
  const btn = document.getElementById('tsConnectBtn');
  const result = document.getElementById('tsResult');
  if (!key) {{ result.textContent = 'Please enter an auth key.'; result.style.color = '#fca5a5'; return; }}
  btn.disabled = true; btn.textContent = 'Connecting...';
  result.textContent = ''; result.style.color = '';
  try {{
    const payload = {{ authKey: key }};
    if (subnet) payload.subnetCIDR = subnet;
    const resp = await fetch('/api/tailscale/setup', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(payload)
    }});
    const data = await resp.json();
    if (data.ok) {{
      result.textContent = 'Connected! IP: ' + (data.ip || 'resolving...');
      result.style.color = '#33e680';
      btn.textContent = 'Connected ✓';
      // Poll until the dashboard shows Tailscale as online, then reload
      let polls = 0;
      const pollReload = setInterval(async () => {{
        polls++;
        try {{
          const sr = await fetch('/api/tailscale/status');
          const sd = await sr.json();
          if (sd.online && sd.ip) {{
            result.textContent = 'Connected! IP: ' + sd.ip;
            clearInterval(pollReload);
            result.textContent += ' (connected)';
          }} else if (polls >= 15) {{
            clearInterval(pollReload);
            result.textContent = 'Connected. IP may still be resolving.';
          }}
        }} catch (_) {{
          if (polls >= 15) {{ clearInterval(pollReload); result.textContent = 'Connected. Refresh later to verify status.'; }}
        }}
      }}, 2000);
    }} else {{
      result.textContent = data.error || 'Setup failed';
      result.style.color = '#fca5a5';
    }}
  }} catch (e) {{
    result.textContent = 'Network error: ' + e.message;
    result.style.color = '#fca5a5';
  }}
  btn.disabled = false; btn.textContent = 'Connect Tailscale';
}}

function _tsResult() {{
  return document.getElementById('tsControlResult') || document.getElementById('tsToggleResult');
}}
async function turnOffTailscale() {{
  const result = _tsResult();
  result.textContent = 'Turning off...'; result.style.color = '';
  try {{
    const resp = await fetch('/api/tailscale/down', {{ method: 'POST', headers: {{'Content-Type': 'application/json'}} }});
    const data = await resp.json();
    if (data.ok) {{
      result.textContent = 'Turned off.'; result.style.color = '#33e680';
    }} else {{
      result.textContent = data.error || 'Failed'; result.style.color = '#fca5a5';
    }}
  }} catch (e) {{
    result.textContent = 'Error: ' + e.message; result.style.color = '#fca5a5';
  }}
}}
async function turnOnTailscale() {{
  const result = _tsResult();
  result.textContent = 'Connecting...'; result.style.color = '';
  try {{
    const resp = await fetch('/api/tailscale/up', {{ method: 'POST', headers: {{'Content-Type': 'application/json'}} }});
    const data = await resp.json();
    if (data.ok) {{
      result.textContent = 'Connected!'; result.style.color = '#33e680';
    }} else {{
      result.textContent = data.error || 'Failed'; result.style.color = '#fca5a5';
    }}
  }} catch (e) {{
    result.textContent = 'Error: ' + e.message; result.style.color = '#fca5a5';
  }}
}}
async function disconnectTailscale() {{
  if (!confirm('Disconnect permanently? This removes the gateway from your Tailscale account. You will need to enter an auth key again to reconnect.')) return;
  const result = _tsResult();
  result.textContent = 'Disconnecting...'; result.style.color = '';
  try {{
    const resp = await fetch('/api/tailscale/logout', {{ method: 'POST', headers: {{'Content-Type': 'application/json'}} }});
    const data = await resp.json();
    if (data.ok) {{
      result.textContent = 'Disconnected.'; result.style.color = '#33e680';
    }} else {{
      result.textContent = data.error || 'Failed'; result.style.color = '#fca5a5';
    }}
  }} catch (e) {{
    result.textContent = 'Error: ' + e.message; result.style.color = '#fca5a5';
  }}
}}

function markSetupComplete() {{
  localStorage.setItem('hw_setup_complete', '1');
  const guide = document.getElementById('setupGuide');
  if (guide) guide.open = false;
  document.getElementById('setupGuideTitle').textContent = 'Setup Complete';
  updateSetupGuideTap();
  const btn = document.getElementById('setupCompleteBtn');
  if (btn) btn.style.display = 'none';
}}
function restoreSetupComplete() {{
  if (localStorage.getItem('hw_setup_complete') === '1') {{
    const guide = document.getElementById('setupGuide');
    if (guide) guide.open = false;
    document.getElementById('setupGuideTitle').textContent = 'Setup Complete';
    updateSetupGuideTap();
    const btn = document.getElementById('setupCompleteBtn');
    if (btn) btn.style.display = 'none';
  }}
}}
function updateSetupGuideTap() {{
  const guide = document.getElementById('setupGuide');
  const tap = document.getElementById('setupGuideTap');
  if (!guide || !tap) return;
  tap.textContent = guide.open ? 'collapse' : 'tap to expand';
}}
restoreSetupComplete();
const _setupGuide = document.getElementById('setupGuide');
if (_setupGuide) _setupGuide.addEventListener('toggle', updateSetupGuideTap);
updateSetupGuideTap();
restoreStepDone('step1', 'step1badge', 'step1DoneBtn', 'hw_step1_done');
restoreStepDone('step5', 'step5badge', 'step5DoneBtn', 'hw_step5_done');

let _lastState = null;
async function pollStatus() {{
  try {{
    const [statusResp, networkResp] = await Promise.all([
      fetch('/api/status'),
      fetch('/api/network')
    ]);
    const data = await statusResp.json();
    const net = await networkResp.json();
    const ts = data.tailscale || {{}};
    const key = [ts.online, ts.routesPending, ts.routesApproved, ts.keyExpired, ts.keyExpiringSoon].join(',');
    _lastState = key;

    const routesApproved = !!ts.routesApproved;
    const tsOnline = !!ts.online;
    const routesPending = !!ts.routesPending;
    const tsIp = ts.ip || '-';
    const tsHostname = ts.hostname || '-';
    const localIp = net.localIp || '-';
    const localNetwork = net.detectedSubnet || '-';

    const statusBadge = document.getElementById('statusBadge');
    if (statusBadge) {{
      statusBadge.classList.remove('badge-green', 'badge-red');
      if (tsOnline) {{
        statusBadge.classList.add('badge-green');
        statusBadge.textContent = 'Connected';
      }} else {{
        statusBadge.classList.add('badge-red');
        statusBadge.textContent = ts.installed ? 'Offline' : 'Not Installed';
      }}
    }}

    const localIpEl = document.getElementById('statusLocalIp');
    if (localIpEl) localIpEl.textContent = localIp;
    const localNetEl = document.getElementById('statusLocalNetwork');
    if (localNetEl) localNetEl.textContent = localNetwork;
    const tsIpEl = document.getElementById('statusTailscaleIp');
    if (tsIpEl) tsIpEl.textContent = tsIp;
    const tsHostEl = document.getElementById('statusTailscaleHostname');
    if (tsHostEl) tsHostEl.textContent = tsHostname;

    const tsBadge = document.getElementById('tsBadge');
    if (tsBadge) {{
      tsBadge.classList.remove('badge-green', 'badge-red', 'badge-yellow');
      if (tsOnline && routesPending) {{
        tsBadge.classList.add('badge-yellow');
        tsBadge.textContent = 'Routes Pending';
      }} else if (tsOnline) {{
        tsBadge.classList.add('badge-green');
        tsBadge.textContent = 'Connected';
      }} else {{
        tsBadge.classList.add('badge-red');
        tsBadge.textContent = ts.installed ? 'Offline' : 'Not Installed';
      }}
    }}

    const tsSubtitle = document.getElementById('tsSubtitle');
    if (tsSubtitle) {{
      if (tsOnline) {{
        tsSubtitle.innerHTML = `IP: <code>${{tsIp}}</code> &middot; Subnet: <code>${{localNetwork}}</code>`;
      }} else {{
        tsSubtitle.textContent = 'Remote access is off.';
      }}
    }}

    const topWarn = document.getElementById('tsStep4Warning');
    if (topWarn) topWarn.style.display = (tsOnline && !routesApproved) ? 'block' : 'none';

    const inlineWarn = document.getElementById('step4InlineWarning');
    if (inlineWarn) inlineWarn.style.display = (tsOnline && !routesApproved) ? 'block' : 'none';

    const step4Row = document.getElementById('step4Row');
    if (step4Row) {{
      step4Row.classList.remove('step-highlight', 'step-completed');
      if (routesApproved) step4Row.classList.add('step-completed');
      else if (tsOnline) step4Row.classList.add('step-highlight');
    }}

    const step4Badge = document.getElementById('step4badge');
    if (step4Badge) {{
      step4Badge.classList.remove('step-action', 'step-done');
      if (routesApproved) {{
        step4Badge.classList.add('step-done');
        step4Badge.innerHTML = '&#10003;';
      }} else {{
        if (tsOnline) step4Badge.classList.add('step-action');
        step4Badge.textContent = '4';
      }}
    }}

    const tsCard = document.getElementById('tsControlCard');
    if (tsCard) {{
      tsCard.style.border = (tsOnline && !routesApproved) ? '1px solid rgba(245,158,11,0.4)' : '';
    }}
  }} catch (e) {{}}
}}
setInterval(pollStatus, 3000);
</script>
</body></html>"""

    # ── Polling loop ─────────────────────────────────────────

    def poll_loop(self) -> None:
        while True:
            if self.paired and self.bitaxe_host.strip():
                try:
                    result = self.fetch_paired_miner()
                    if result:
                        normalized = self.normalize(result["data"])
                        self.state.set_poll_success({"raw": result["data"], "normalized": normalized})
                    else:
                        self.state.set_poll_error("Miner unreachable")
                except Exception as exc:
                    self.state.set_poll_error(str(exc))
            time.sleep(self.poll_seconds)

    def run(self) -> None:
        print(f"[{now_iso()}] HashWatcher Gateway Agent starting", flush=True)
        print(f"[{now_iso()}] Agent ID: {self.agent_id} | Host: {self.hostname}", flush=True)
        print(f"[{now_iso()}] Miner: {self.bitaxe_host or '(none)'} | Poll: {self.poll_seconds}s", flush=True)

        poll_thread = threading.Thread(target=self.poll_loop, daemon=True)
        poll_thread.start()

        self.start_server()


if __name__ == "__main__":
    agent = HubAgent()
    agent.run()
