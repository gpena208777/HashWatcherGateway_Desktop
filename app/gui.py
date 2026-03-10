#!/usr/bin/env python3
"""Desktop GUI for HashWatcher Gateway with guided API-first onboarding."""

from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Dict, Optional


DEFAULT_HOSTNAME = "HashWatcherGatewayDesktop"
DEFAULT_PORT = "8787"
DEFAULT_BIND = "0.0.0.0"


class GatewayGui:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("HashWatcher Gateway Desktop")
        self.root.geometry("1040x780")
        self.root.minsize(940, 680)

        self.proc: Optional[subprocess.Popen] = None
        self.log_queue: "queue.Queue[str]" = queue.Queue()

        self.repo_root = Path(__file__).resolve().parent.parent
        self.app_root = self.repo_root / "app"
        self.main_py = self.app_root / "main.py"
        self.settings_path = Path.home() / ".hashwatcher-gateway-desktop" / "gui_settings.json"

        self.hostname_var = tk.StringVar(value=DEFAULT_HOSTNAME)
        self.port_var = tk.StringVar(value=DEFAULT_PORT)
        self.bind_var = tk.StringVar(value=DEFAULT_BIND)
        self.status_var = tk.StringVar(value="Gateway: stopped")

        self.ts_tailnet_var = tk.StringVar(value="")
        self.ts_api_key_var = tk.StringVar(value="")
        self.ts_auth_key_var = tk.StringVar(value="")
        self.ts_subnet_var = tk.StringVar(value="")

        self.ts_state_var = tk.StringVar(value="Tailscale: unknown")
        self.ts_ip_var = tk.StringVar(value="Tailscale IP: -")
        self.ts_routes_var = tk.StringVar(value="Advertised Routes: -")
        self.ts_route_approval_var = tk.StringVar(value="Route Approval: -")

        self.wizard_step = 0
        self.wizard_steps = [
            {
                "title": "Step 1 of 5: Start Gateway",
                "body": "Start the local gateway service.",
                "action": "Start Gateway",
                "hint": "Gateway must be running before onboarding can continue.",
            },
            {
                "title": "Step 2 of 5: Generate Auth Key (API)",
                "body": "Enter Tailnet + API key, then generate a reusable auth key inside this app.",
                "action": "Generate Auth Key via API",
                "hint": "No manual key creation page required.",
            },
            {
                "title": "Step 3 of 5: Connect Gateway",
                "body": "Use generated auth key to connect this gateway to Tailscale.",
                "action": "Connect Tailscale",
                "hint": "You can also enter your own auth key manually.",
            },
            {
                "title": "Step 4 of 5: Approve Route",
                "body": "Approve advertised subnet route in Tailscale Machines page.",
                "action": "Open Machines Page",
                "hint": "Approve route for machine HashWatcherGatewayDesktop.",
            },
            {
                "title": "Step 5 of 5: Verify Complete",
                "body": "Refresh and verify online + authenticated + route approved.",
                "action": "Refresh Status",
                "hint": "Setup completes when Route Approval shows approved.",
            },
        ]

        self._load_settings()
        self._build_layout()
        self._render_wizard_step()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(250, self._drain_log_queue)
        self.root.after(750, self._refresh_status)
        self.root.after(1200, self.refresh_tailscale_status)

    # ----- UI -----
    def _build_layout(self) -> None:
        root_frame = ttk.Frame(self.root, padding=12)
        root_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            root_frame,
            text="HashWatcher Gateway Desktop",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(root_frame, textvariable=self.status_var, foreground="#0a7f38").pack(anchor="w", pady=(4, 8))

        notebook = ttk.Notebook(root_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        gateway_tab = ttk.Frame(notebook, padding=12)
        onboarding_tab = ttk.Frame(notebook, padding=12)
        notebook.add(gateway_tab, text="Gateway")
        notebook.add(onboarding_tab, text="Guided Onboarding")

        self._build_gateway_tab(gateway_tab)
        self._build_onboarding_tab(onboarding_tab)
        self._log("GUI ready. Use Guided Onboarding tab.")

    def _build_gateway_tab(self, frame: ttk.Frame) -> None:
        controls = ttk.Frame(frame)
        controls.grid(row=0, column=0, sticky="ew")

        ttk.Label(controls, text="Machine Name").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.hostname_var, width=30).grid(row=0, column=1, sticky="w", padx=(6, 14))

        ttk.Label(controls, text="Bind").grid(row=0, column=2, sticky="w")
        ttk.Entry(controls, textvariable=self.bind_var, width=14).grid(row=0, column=3, sticky="w", padx=(6, 14))

        ttk.Label(controls, text="Port").grid(row=0, column=4, sticky="w")
        ttk.Entry(controls, textvariable=self.port_var, width=8).grid(row=0, column=5, sticky="w", padx=(6, 14))

        self.start_btn = ttk.Button(controls, text="Start Gateway", command=self.start_gateway)
        self.start_btn.grid(row=1, column=0, sticky="w", pady=(12, 10))

        self.stop_btn = ttk.Button(controls, text="Stop Gateway", command=self.stop_gateway, state=tk.DISABLED)
        self.stop_btn.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(12, 10))

        ttk.Button(controls, text="Open Dashboard", command=self.open_dashboard).grid(
            row=1, column=2, sticky="w", padx=(18, 0), pady=(12, 10)
        )
        ttk.Button(controls, text="Refresh Status", command=self.refresh_tailscale_status).grid(
            row=1, column=3, sticky="w", padx=(8, 0), pady=(12, 10)
        )

        ttk.Label(frame, text="Gateway Logs").grid(row=1, column=0, sticky="w", pady=(8, 6))
        self.log_text = ScrolledText(frame, wrap=tk.WORD, height=24, font=("Menlo", 10))
        self.log_text.grid(row=2, column=0, sticky="nsew")

        frame.rowconfigure(2, weight=1)
        frame.columnconfigure(0, weight=1)

    def _build_onboarding_tab(self, frame: ttk.Frame) -> None:
        wizard_card = ttk.LabelFrame(frame, text="Setup Wizard", padding=12)
        wizard_card.grid(row=0, column=0, columnspan=2, sticky="nsew")

        self.wizard_title_var = tk.StringVar(value="")
        self.wizard_body_var = tk.StringVar(value="")
        self.wizard_hint_var = tk.StringVar(value="")

        ttk.Label(wizard_card, textvariable=self.wizard_title_var, font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(wizard_card, textvariable=self.wizard_body_var, wraplength=900).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 6)
        )
        ttk.Label(wizard_card, textvariable=self.wizard_hint_var, wraplength=900, foreground="#666666").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(0, 10)
        )

        self.wizard_action_btn = ttk.Button(wizard_card, text="", command=self._run_wizard_action)
        self.wizard_action_btn.grid(row=3, column=0, sticky="w")

        self.wizard_back_btn = ttk.Button(wizard_card, text="Back", command=self._wizard_back)
        self.wizard_back_btn.grid(row=3, column=1, sticky="e")

        self.wizard_next_btn = ttk.Button(wizard_card, text="Next", command=self._wizard_next)
        self.wizard_next_btn.grid(row=3, column=2, sticky="e", padx=(8, 0))

        details = ttk.LabelFrame(frame, text="Connection Details", padding=12)
        details.grid(row=1, column=0, sticky="nsew", pady=(10, 0))

        ttk.Label(details, text="Tailnet (example: example.com or your-tailnet)").grid(row=0, column=0, sticky="w")
        ttk.Entry(details, textvariable=self.ts_tailnet_var, width=44).grid(row=1, column=0, sticky="ew", pady=(4, 8))

        ttk.Label(details, text="Tailscale API Key (for key generation)").grid(row=2, column=0, sticky="w")
        ttk.Entry(details, textvariable=self.ts_api_key_var, width=66, show="*").grid(row=3, column=0, sticky="ew", pady=(4, 8))

        ttk.Button(details, text="Generate Auth Key via API", command=self.generate_auth_key_via_api).grid(
            row=4, column=0, sticky="w", pady=(2, 10)
        )

        ttk.Label(details, text="Auth Key (tskey-auth-...) ").grid(row=5, column=0, sticky="w")
        ttk.Entry(details, textvariable=self.ts_auth_key_var, width=66, show="*").grid(row=6, column=0, sticky="ew", pady=(4, 8))

        ttk.Label(details, text="Subnet CIDR (optional)").grid(row=7, column=0, sticky="w")
        ttk.Entry(details, textvariable=self.ts_subnet_var, width=24).grid(row=8, column=0, sticky="w", pady=(4, 0))

        links = ttk.Frame(details)
        links.grid(row=9, column=0, sticky="w", pady=(10, 0))
        ttk.Button(links, text="Open API Keys Page", command=self.open_tailscale_keys).grid(row=0, column=0, sticky="w")
        ttk.Button(links, text="Open Machines Page", command=self.open_tailscale_machines).grid(row=0, column=1, sticky="w", padx=(8, 0))

        status = ttk.LabelFrame(frame, text="Live Status", padding=12)
        status.grid(row=1, column=1, sticky="nsew", padx=(12, 0), pady=(10, 0))
        ttk.Label(status, textvariable=self.ts_state_var).grid(row=0, column=0, sticky="w")
        ttk.Label(status, textvariable=self.ts_ip_var).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(status, textvariable=self.ts_routes_var).grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Label(status, textvariable=self.ts_route_approval_var).grid(row=3, column=0, sticky="w", pady=(4, 0))

        action_row = ttk.Frame(frame)
        action_row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(action_row, text="Connect", command=self.connect_tailscale).grid(row=0, column=0, sticky="w")
        ttk.Button(action_row, text="Turn On", command=self.turn_on_tailscale).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(action_row, text="Turn Off", command=self.turn_off_tailscale).grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Button(action_row, text="Disconnect", command=self.disconnect_tailscale).grid(row=0, column=3, sticky="w", padx=(8, 0))
        ttk.Button(action_row, text="Refresh Status", command=self.refresh_tailscale_status).grid(row=0, column=4, sticky="w", padx=(16, 0))

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(1, weight=1)

    # ----- persistence -----
    def _load_settings(self) -> None:
        if not self.settings_path.exists():
            return
        try:
            with self.settings_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.hostname_var.set(str(data.get("hostname") or DEFAULT_HOSTNAME))
            self.port_var.set(str(data.get("port") or DEFAULT_PORT))
            self.bind_var.set(str(data.get("bind") or DEFAULT_BIND))
            self.ts_subnet_var.set(str(data.get("subnet") or ""))
            self.ts_tailnet_var.set(str(data.get("tailnet") or ""))
            self.ts_auth_key_var.set(str(data.get("authKey") or ""))
            # API key intentionally not persisted.
        except Exception:
            pass

    def _save_settings(self) -> None:
        payload = {
            "hostname": self.hostname_var.get().strip() or DEFAULT_HOSTNAME,
            "port": self.port_var.get().strip() or DEFAULT_PORT,
            "bind": self.bind_var.get().strip() or DEFAULT_BIND,
            "subnet": self.ts_subnet_var.get().strip(),
            "tailnet": self.ts_tailnet_var.get().strip(),
            "authKey": self.ts_auth_key_var.get().strip(),
        }
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        with self.settings_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    # ----- logs/process -----
    def _log(self, message: str) -> None:
        self.log_text.insert(tk.END, message.rstrip() + "\n")
        self.log_text.see(tk.END)

    def _read_process_output(self, proc: subprocess.Popen) -> None:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            self.log_queue.put(line.rstrip("\n"))
        self.log_queue.put("[gateway process exited]")

    def _drain_log_queue(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._log(line)
        self.root.after(200, self._drain_log_queue)

    def _refresh_status(self) -> None:
        running = self.proc is not None and self.proc.poll() is None
        if running:
            self.status_var.set("Gateway: running")
            self.start_btn.configure(state=tk.DISABLED)
            self.stop_btn.configure(state=tk.NORMAL)
        else:
            self.status_var.set("Gateway: stopped")
            self.start_btn.configure(state=tk.NORMAL)
            self.stop_btn.configure(state=tk.DISABLED)
            if self.proc is not None and self.proc.poll() is not None:
                self.proc = None
        self.root.after(800, self._refresh_status)

    # ----- gateway/api -----
    def _validated_port(self) -> str:
        raw = self.port_var.get().strip() or DEFAULT_PORT
        try:
            port = int(raw)
        except ValueError as exc:
            raise ValueError("Port must be a number.") from exc
        if port < 1 or port > 65535:
            raise ValueError("Port must be between 1 and 65535.")
        return str(port)

    def _base_url(self) -> str:
        try:
            port = self._validated_port()
        except ValueError:
            port = DEFAULT_PORT
        return f"http://127.0.0.1:{port}"

    def _api_get(self, path: str) -> Dict[str, Any]:
        req = urllib.request.Request(url=self._base_url() + path, method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _api_post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url=self._base_url() + path,
            method="POST",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def start_gateway(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        try:
            port = self._validated_port()
        except ValueError as exc:
            messagebox.showerror("Invalid Port", str(exc))
            return

        hostname = self.hostname_var.get().strip() or DEFAULT_HOSTNAME
        bind = self.bind_var.get().strip() or DEFAULT_BIND
        self.hostname_var.set(hostname)
        self.bind_var.set(bind)
        self.port_var.set(port)
        self._save_settings()

        env = os.environ.copy()
        env["PI_HOSTNAME"] = hostname
        env["STATUS_HTTP_PORT"] = port
        env["STATUS_HTTP_BIND"] = bind

        try:
            self.proc = subprocess.Popen(
                [sys.executable, str(self.main_py)],
                cwd=str(self.app_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            messagebox.showerror("Failed to Start", str(exc))
            self.proc = None
            return

        threading.Thread(target=self._read_process_output, args=(self.proc,), daemon=True).start()
        self._log(f"[gui] started gateway on {bind}:{port} as {hostname}")
        self.root.after(1200, self.refresh_tailscale_status)

    def stop_gateway(self) -> None:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            self.proc = None
            return
        self._log("[gui] stopping gateway...")
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        self._log("[gui] gateway stopped.")
        self.proc = None

    # ----- tailscale api + status -----
    def generate_auth_key_via_api(self) -> None:
        tailnet = self.ts_tailnet_var.get().strip()
        api_key = self.ts_api_key_var.get().strip()
        if not tailnet:
            messagebox.showwarning("Missing Tailnet", "Enter Tailnet first (example: your-tailnet or example.com).")
            return
        if not api_key:
            messagebox.showwarning("Missing API Key", "Enter Tailscale API key first.")
            return

        url_tailnet = urllib.parse.quote(tailnet, safe="")
        url = f"https://api.tailscale.com/api/v2/tailnet/{url_tailnet}/keys"

        payload = {
            "capabilities": {
                "devices": {
                    "create": {
                        "reusable": True,
                        "ephemeral": False,
                        "preauthorized": True,
                        "tags": ["tag:hashwatcher-gateway"],
                    }
                }
            },
            "expirySeconds": 60 * 60 * 24 * 30,
            "description": "HashWatcherGatewayDesktop",
        }

        auth = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("utf-8")
        req = urllib.request.Request(
            url=url,
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            messagebox.showerror("API Error", detail or str(exc))
            return
        except Exception as exc:
            messagebox.showerror("API Error", str(exc))
            return

        key = str(data.get("key") or "").strip()
        if not key:
            messagebox.showerror("API Error", "No auth key returned by Tailscale API.")
            return

        self.ts_auth_key_var.set(key)
        self._save_settings()
        self._log("[gui] generated auth key via Tailscale API.")
        messagebox.showinfo("Auth Key Generated", "Auth key generated and filled in automatically.")

    def refresh_tailscale_status(self) -> None:
        try:
            data = self._api_get("/api/status")
        except urllib.error.URLError as exc:
            self.ts_state_var.set(f"Tailscale: gateway unreachable ({exc.reason})")
            self.ts_ip_var.set("Tailscale IP: -")
            self.ts_routes_var.set("Advertised Routes: -")
            self.ts_route_approval_var.set("Route Approval: -")
            return
        except Exception as exc:
            self.ts_state_var.set(f"Tailscale: status error ({exc})")
            return

        tailscale = data.get("tailscale") or {}
        network = data.get("network") or {}

        online = bool(tailscale.get("online"))
        authenticated = bool(tailscale.get("authenticated"))
        ip = tailscale.get("ip") or "-"
        routes = tailscale.get("advertisedRoutes") or []
        routes_approved = bool(tailscale.get("routesApproved"))
        routes_pending = bool(tailscale.get("routesPending"))
        key_expired = bool(tailscale.get("keyExpired"))

        state_bits = ["online" if online else "offline", "authenticated" if authenticated else "not authenticated"]
        if key_expired:
            state_bits.append("key expired")
        self.ts_state_var.set("Tailscale: " + " | ".join(state_bits))
        self.ts_ip_var.set(f"Tailscale IP: {ip}")

        routes_str = ", ".join(str(route) for route in routes) if routes else "-"
        self.ts_routes_var.set(f"Advertised Routes: {routes_str}")

        detected_subnet = network.get("detectedSubnet") or "-"
        if routes_approved:
            approval = f"approved (subnet {detected_subnet})"
        elif routes_pending:
            approval = f"pending approval in Tailscale admin (subnet {detected_subnet})"
        else:
            approval = "not yet configured"
        self.ts_route_approval_var.set(f"Route Approval: {approval}")

    def connect_tailscale(self) -> None:
        auth_key = self.ts_auth_key_var.get().strip()
        subnet = self.ts_subnet_var.get().strip()
        if not auth_key:
            messagebox.showwarning("Missing Auth Key", "Generate or paste auth key first.")
            return

        payload: Dict[str, Any] = {"authKey": auth_key}
        if subnet:
            payload["subnetCIDR"] = subnet

        self._save_settings()

        try:
            resp = self._api_post("/api/tailscale/setup", payload)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            messagebox.showerror("Connect Failed", detail or str(exc))
            return
        except Exception as exc:
            messagebox.showerror("Connect Failed", str(exc))
            return

        if resp.get("ok"):
            self._log("[gui] tailscale connect requested.")
            self.refresh_tailscale_status()
            messagebox.showinfo("Tailscale", "Connect request sent.")
        else:
            messagebox.showerror("Tailscale Error", str(resp.get("error") or "Unknown error"))

    def turn_on_tailscale(self) -> None:
        self._post_simple_tailscale("/api/tailscale/up", "Turn On")

    def turn_off_tailscale(self) -> None:
        self._post_simple_tailscale("/api/tailscale/down", "Turn Off")

    def disconnect_tailscale(self) -> None:
        if not messagebox.askyesno("Disconnect", "Disconnect gateway from Tailscale?"):
            return
        self._post_simple_tailscale("/api/tailscale/logout", "Disconnect")

    def _post_simple_tailscale(self, path: str, action_name: str) -> None:
        try:
            resp = self._api_post(path, {})
        except Exception as exc:
            messagebox.showerror(action_name, str(exc))
            return
        if resp.get("ok"):
            self._log(f"[gui] tailscale action succeeded: {action_name.lower()}")
            self.refresh_tailscale_status()
        else:
            messagebox.showerror(action_name, str(resp.get("error") or "Unknown error"))

    # ----- wizard -----
    def _wizard_back(self) -> None:
        if self.wizard_step > 0:
            self.wizard_step -= 1
            self._render_wizard_step()

    def _wizard_next(self) -> None:
        if not self._wizard_step_complete(self.wizard_step):
            return
        if self.wizard_step < len(self.wizard_steps) - 1:
            self.wizard_step += 1
            self._render_wizard_step()
        else:
            messagebox.showinfo("Setup Complete", "HashWatcher gateway onboarding is complete.")

    def _render_wizard_step(self) -> None:
        step = self.wizard_steps[self.wizard_step]
        self.wizard_title_var.set(step["title"])
        self.wizard_body_var.set(step["body"])
        self.wizard_hint_var.set(step["hint"])
        self.wizard_action_btn.configure(text=step["action"])
        self.wizard_back_btn.configure(state=tk.NORMAL if self.wizard_step > 0 else tk.DISABLED)
        self.wizard_next_btn.configure(text="Finish" if self.wizard_step == len(self.wizard_steps) - 1 else "Next")

    def _wizard_step_complete(self, step_index: int) -> bool:
        self.refresh_tailscale_status()

        if step_index == 0:
            running = self.proc is not None and self.proc.poll() is None
            if not running:
                messagebox.showinfo("Step Incomplete", "Start the gateway first.")
                return False
        elif step_index == 1:
            key = self.ts_auth_key_var.get().strip()
            if not key.startswith("tskey-"):
                messagebox.showinfo("Step Incomplete", "Generate or paste a valid auth key first.")
                return False
        elif step_index == 2:
            state = self.ts_state_var.get().lower()
            if "authenticated" not in state:
                messagebox.showinfo("Step Incomplete", "Connect Tailscale successfully first.")
                return False
        elif step_index == 3:
            approval = self.ts_route_approval_var.get().lower()
            if "approved" not in approval:
                messagebox.showinfo("Step Incomplete", "Approve routes in Machines page, then refresh status.")
                return False
        elif step_index == 4:
            approval = self.ts_route_approval_var.get().lower()
            state = self.ts_state_var.get().lower()
            if "approved" not in approval or "online" not in state:
                messagebox.showinfo("Not Ready", "Need online + route approved.")
                return False
        return True

    def _run_wizard_action(self) -> None:
        idx = self.wizard_step
        if idx == 0:
            self.start_gateway()
        elif idx == 1:
            self.generate_auth_key_via_api()
        elif idx == 2:
            self.connect_tailscale()
        elif idx == 3:
            self.open_tailscale_machines()
        elif idx == 4:
            self.refresh_tailscale_status()

    # ----- links / app lifecycle -----
    def open_dashboard(self) -> None:
        webbrowser.open(self._base_url())

    @staticmethod
    def open_tailscale_keys() -> None:
        webbrowser.open("https://login.tailscale.com/admin/settings/keys")

    @staticmethod
    def open_tailscale_machines() -> None:
        webbrowser.open("https://login.tailscale.com/admin/machines")

    def _on_close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            if not messagebox.askyesno("Exit", "Gateway is still running. Stop it and close the GUI?"):
                return
            self.stop_gateway()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    GatewayGui().run()


if __name__ == "__main__":
    main()
