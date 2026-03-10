#!/usr/bin/env python3
"""Desktop GUI for HashWatcher Gateway."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Optional


DEFAULT_HOSTNAME = "HashWatcherGatewayDesktop"
DEFAULT_PORT = "8787"
DEFAULT_BIND = "0.0.0.0"


class GatewayGui:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("HashWatcher Gateway Desktop")
        self.root.geometry("920x620")
        self.root.minsize(860, 560)

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

        self._load_settings()
        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(250, self._drain_log_queue)
        self.root.after(750, self._refresh_status)

    def _build_layout(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(
            frame,
            text="HashWatcher Gateway Desktop",
            font=("Segoe UI", 15, "bold"),
        )
        title.grid(row=0, column=0, columnspan=8, sticky="w")

        ttk.Label(frame, textvariable=self.status_var, foreground="#0a7f38").grid(
            row=1, column=0, columnspan=8, sticky="w", pady=(4, 10)
        )

        ttk.Label(frame, text="Machine Name").grid(row=2, column=0, sticky="w")
        hostname_entry = ttk.Entry(frame, textvariable=self.hostname_var, width=30)
        hostname_entry.grid(row=2, column=1, sticky="w", padx=(6, 12))

        ttk.Label(frame, text="Bind").grid(row=2, column=2, sticky="w")
        bind_entry = ttk.Entry(frame, textvariable=self.bind_var, width=14)
        bind_entry.grid(row=2, column=3, sticky="w", padx=(6, 12))

        ttk.Label(frame, text="Port").grid(row=2, column=4, sticky="w")
        port_entry = ttk.Entry(frame, textvariable=self.port_var, width=8)
        port_entry.grid(row=2, column=5, sticky="w", padx=(6, 12))

        self.start_btn = ttk.Button(frame, text="Start Gateway", command=self.start_gateway)
        self.start_btn.grid(row=3, column=0, sticky="w", pady=(12, 10))

        self.stop_btn = ttk.Button(frame, text="Stop Gateway", command=self.stop_gateway, state=tk.DISABLED)
        self.stop_btn.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(12, 10))

        ttk.Button(frame, text="Open Dashboard", command=self.open_dashboard).grid(
            row=3, column=2, sticky="w", padx=(18, 0), pady=(12, 10)
        )
        ttk.Button(frame, text="Tailscale Keys", command=self.open_tailscale_keys).grid(
            row=3, column=3, sticky="w", padx=(8, 0), pady=(12, 10)
        )
        ttk.Button(frame, text="Tailscale Machines", command=self.open_tailscale_machines).grid(
            row=3, column=4, sticky="w", padx=(8, 0), pady=(12, 10)
        )

        ttk.Label(frame, text="Gateway Logs").grid(row=4, column=0, columnspan=8, sticky="w", pady=(8, 6))
        self.log_text = ScrolledText(frame, wrap=tk.WORD, height=26, font=("Menlo", 10))
        self.log_text.grid(row=5, column=0, columnspan=8, sticky="nsew")

        frame.rowconfigure(5, weight=1)
        for col in range(8):
            frame.columnconfigure(col, weight=1 if col == 7 else 0)

        self._log("GUI ready. Click 'Start Gateway' to launch.")

    def _load_settings(self) -> None:
        if not self.settings_path.exists():
            return
        try:
            with self.settings_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.hostname_var.set(str(data.get("hostname") or DEFAULT_HOSTNAME))
            self.port_var.set(str(data.get("port") or DEFAULT_PORT))
            self.bind_var.set(str(data.get("bind") or DEFAULT_BIND))
        except Exception:
            pass

    def _save_settings(self) -> None:
        payload = {
            "hostname": self.hostname_var.get().strip() or DEFAULT_HOSTNAME,
            "port": self.port_var.get().strip() or DEFAULT_PORT,
            "bind": self.bind_var.get().strip() or DEFAULT_BIND,
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

    def open_dashboard(self) -> None:
        try:
            port = self._validated_port()
        except ValueError:
            port = DEFAULT_PORT
        webbrowser.open(f"http://127.0.0.1:{port}")

    @staticmethod
    def open_tailscale_keys() -> None:
        webbrowser.open("https://login.tailscale.com/admin/settings/keys")

    @staticmethod
    def open_tailscale_machines() -> None:
        webbrowser.open("https://login.tailscale.com/admin/machines")

    def _on_close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            if not messagebox.askyesno(
                "Exit",
                "Gateway is still running. Stop it and close the GUI?",
            ):
                return
            self.stop_gateway()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    GatewayGui().run()


if __name__ == "__main__":
    main()

