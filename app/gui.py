#!/usr/bin/env python3
"""Desktop GUI for HashWatcher Gateway with guided onboarding wizard."""

from __future__ import annotations

import json
import os
import platform
import queue
import subprocess
import sys
import threading
import tkinter as tk
import urllib.error
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
        self.root.geometry("1020x760")
        self.root.minsize(930, 660)

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

        self.ts_auth_key_var = tk.StringVar(value="")
        self.ts_subnet_var = tk.StringVar(value="")
        self.ts_state_var = tk.StringVar(value="Tailscale: unknown")
        self.ts_ip_var = tk.StringVar(value="Tailscale IP: -")
        self.ts_routes_var = tk.StringVar(value="Advertised Routes: -")
        self.ts_route_approval_var = tk.StringVar(value="Route Approval: -")

        self.wizard_step = 0
        self.wizard_steps = [
            {
                "title": "Step 1 of 6: Start Gateway",
                "body": "Start the local gateway service so onboarding can run.",
                "action": "Start Gateway",
                "hint": "Tap Start Gateway, then wait for status to show running.",
            },
            {
                "title": "Step 2 of 6: Start Tailscale",
                "body": "Launch local Tailscale and make sure you are signed in.",
                "action": "Launch Tailscale",
                "hint": "This opens or starts Tailscale on your machine.",
            },
            {
                "title": "Step 3 of 6: Generate Auth Key",
                "body": "Open Tailscale admin and generate a reusable auth key.",
                "action": "Open Tailscale Keys",
                "hint": "Create a key and copy the tskey-auth-... value.",
            },
            {
                "title": "Step 4 of 6: Connect Gateway",
                "body": "Paste your auth key, optionally set subnet, then connect.",
                "action": "Connect Tailscale",
                "hint": "Default subnet auto-detect is fine unless your LAN is unusual.",
            },
            {
                "title": "Step 5 of 6: Approve Subnet Route",
                "body": "Approve route advertisement in Tailscale Machines page.",
                "action": "Open Machines Page",
                "hint": "Approve route for this machine: HashWatcherGatewayDesktop.",
            },
            {
                "title": "Step 6 of 6: Verify Complete",
                "body": "Refresh status and confirm online + route approved.",
                "action": "Refresh Status",
                "hint": "Setup is complete when Route Approval shows approved.",
            },
        ]

        self._load_settings()
        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(250, self._drain_log_queue)
        self.root.after(750, self._refresh_status)
        self.root.after(1200, self.refresh_tailscale_status)

    def _build_layout(self) -> None:
        root_frame = ttk.Frame(self.root, padding=12)
        root_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            root_frame,
            text="HashWatcher Gateway Desktop",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")

        ttk.Label(root_frame, textvariable=self.status_var, foreground="#0a7f38").pack(
            anchor="w", pady=(4, 8)
        )

        notebook = ttk.Notebook(root_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        gateway_tab = ttk.Frame(notebook, padding=12)
        onboarding_tab = ttk.Frame(notebook, padding=12)
        notebook.add(gateway_tab, text="Gateway")
        notebook.add(onboarding_tab, text="Guided Onboarding")

        self._build_gateway_tab(gateway_tab)
        self._build_onboarding_tab(onboarding_tab)
        self._render_wizard_step()
        self._log("GUI ready. Use Guided Onboarding tab for step-by-step setup.")

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
        ttk.Label(wizard_card, textvariable=self.wizard_body_var, wraplength=860).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 6)
        )
        ttk.Label(wizard_card, textvariable=self.wizard_hint_var, wraplength=860, foreground="#666666").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(0, 10)
        )

        self.wizard_action_btn = ttk.Button(wizard_card, text="", command=self._run_wizard_action)
        self.wizard_action_btn.grid(row=3, column=0, sticky="w")

        self.wizard_back_btn = ttk.Button(wizard_card, text="Back", command=self._wizard_back)
        self.wizard_back_btn.grid(row=3, column=1, sticky="e")

        self.wizard_next_btn = ttk.Button(wizard_card, text="Next", command=self._wizard_next)
        self.wizard_next_btn.grid(row=3, column=2, sticky="e", padx=(8, 0))

        form = ttk.LabelFrame(frame, text="Connection Details", padding=12)
        form.grid(row=1, column=0, sticky="nsew", pady=(10, 0))

        ttk.Label(form, text="Auth Key (tskey-auth-...)").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.ts_auth_key_var, width=66, show="*").grid(row=1, column=0, sticky="ew", pady=(4, 8))

        ttk.Label(form, text="Subnet CIDR (optional)").grid(row=2, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.ts_subnet_var, width=24).grid(row=3, column=0, sticky="w", pady=(4, 0))

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
        ttk.Button(action_row, text="Open Keys", command=self.open_tailscale_keys).grid(row=0, column=4, sticky="w", padx=(18, 0))
        ttk.Button(action_row, text="Open Machines", command=self.open_tailscale_machines).grid(row=0, column=5, sticky="w", padx=(8, 0))

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(1, weight=1)

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
        except Exception:
            pass

    def _save_settings(self) -> None:
        payload = {
            "hostname": self.hostname_var.get().strip() or DEFAULT_HOSTNAME,
            "port": self.port_var.get().strip() or DEFAULT_PORT,
            "bind": self.bind_var.get().strip() or DEFAULT_BIND,
            "subnet": self.ts_subnet_var.get().strip(),
        }
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        with self.settings_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

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
        url = self._base_url() + path
        req = urllib.request.Request(url=url, method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = resp.read().decode("utf-8")
        return json.loads(payload)

    def _api_post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self._base_url() + path
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            method="POST",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            response_payload = resp.read().decode("utf-8")
        return json.loads(response_payload)

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

        cmd = [sys.executable, str(self.main_py)]
        try:
            self.proc = subprocess.Popen(
                cmd,
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
        routes_str = ", ".join(str(route) for route in routes) if routes else "-"
        routes_approved = bool(tailscale.get("routesApproved"))
        routes_pending = bool(tailscale.get("routesPending"))
        key_expired = bool(tailscale.get("keyExpired"))

        state_bits = ["online" if online else "offline", "authenticated" if authenticated else "not authenticated"]
        if key_expired:
            state_bits.append("key expired")
        self.ts_state_var.set("Tailscale: " + " | ".join(state_bits))
        self.ts_ip_var.set(f"Tailscale IP: {ip}")
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
            messagebox.showwarning("Missing Auth Key", "Enter your Tailscale auth key first.")
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
            state = self.ts_state_var.get().lower()
            if "gateway unreachable" in state:
                messagebox.showinfo("Step Incomplete", "Gateway is not reachable yet. Start gateway and try again.")
                return False
        elif step_index == 3:
            state = self.ts_state_var.get().lower()
            if "authenticated" not in state:
                messagebox.showinfo("Step Incomplete", "Connect Tailscale successfully first.")
                return False
        elif step_index == 4:
            approval = self.ts_route_approval_var.get().lower()
            if "approved" not in approval:
                messagebox.showinfo("Step Incomplete", "Approve routes in Tailscale Machines page, then refresh status.")
                return False
        elif step_index == 5:
            approval = self.ts_route_approval_var.get().lower()
            state = self.ts_state_var.get().lower()
            if "approved" not in approval or "online" not in state:
                messagebox.showinfo("Not Ready", "Onboarding is not complete yet. Ensure online + route approved.")
                return False
            messagebox.showinfo("Setup Complete", "HashWatcher gateway onboarding is complete.")
        return True

    def _run_wizard_action(self) -> None:
        idx = self.wizard_step
        if idx == 0:
            self.start_gateway()
        elif idx == 1:
            self._launch_local_tailscale()
        elif idx == 2:
            self.open_tailscale_keys()
        elif idx == 3:
            self.connect_tailscale()
        elif idx == 4:
            self.open_tailscale_machines()
        elif idx == 5:
            self.refresh_tailscale_status()

    def _launch_local_tailscale(self) -> None:
        system_name = platform.system()
        try:
            if system_name == "Darwin":
                subprocess.run(["open", "-a", "Tailscale"], check=False)
                self._log("[gui] launched Tailscale app (macOS).")
                messagebox.showinfo("Tailscale", "Tailscale app launched. Sign in if needed.")
            elif system_name == "Windows":
                possible = [
                    r"C:\\Program Files\\Tailscale\\Tailscale.exe",
                    r"C:\\Program Files (x86)\\Tailscale\\Tailscale.exe",
                ]
                launched = False
                for candidate in possible:
                    if os.path.exists(candidate):
                        subprocess.Popen([candidate])
                        launched = True
                        break
                if not launched:
                    subprocess.run(["tailscale", "status"], check=False)
                self._log("[gui] attempted Tailscale launch (Windows).")
                messagebox.showinfo("Tailscale", "Tailscale launch attempted. Sign in if prompted.")
            else:
                subprocess.run(["tailscale", "status"], check=False)
                messagebox.showinfo("Tailscale", "Use your desktop environment to start/sign in to Tailscale.")
        except Exception as exc:
            messagebox.showerror("Tailscale", f"Could not launch Tailscale: {exc}")

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
