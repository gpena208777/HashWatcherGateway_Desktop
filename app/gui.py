#!/usr/bin/env python3
"""PyQt6 desktop GUI for HashWatcher Gateway with guided onboarding."""

from __future__ import annotations

import base64
import json
import os
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6 import QtCore, QtGui, QtWidgets

try:
    from gateway import network_utils
except Exception:  # pragma: no cover - GUI fallback if package import path differs
    network_utils = None  # type: ignore

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional dependency in local runs
    psutil = None  # type: ignore

DEFAULT_HOSTNAME = "HashWatcherGatewayDesktop"
DEFAULT_PORT = "8787"
DEFAULT_BIND = "0.0.0.0"
APP_NAME = "HashWatcher"
WINDOW_TITLE = "HashWatcher Gateway Desktop"


class Var:
    def __init__(self, value: str = "") -> None:
        self._value = str(value)
        self._listeners: list[callable] = []

    def get(self) -> str:
        return self._value

    def set(self, value: Any) -> None:
        text = str(value)
        if text == self._value:
            return
        self._value = text
        for listener in list(self._listeners):
            listener(self._value)

    def bind(self, callback: callable) -> None:
        self._listeners.append(callback)
        callback(self._value)


class BoolVar:
    def __init__(self, value: bool = False) -> None:
        self._value = bool(value)
        self._listeners: list[callable] = []

    def get(self) -> bool:
        return self._value

    def set(self, value: Any) -> None:
        flag = bool(value)
        if flag == self._value:
            return
        self._value = flag
        for listener in list(self._listeners):
            listener(self._value)

    def bind(self, callback: callable) -> None:
        self._listeners.append(callback)
        callback(self._value)


class GatewayGui(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1160, 820)
        self.setMinimumSize(880, 650)

        self.proc: Optional[subprocess.Popen] = None
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self._port_conflict_notified = False
        self.external_gateway_running = False
        self._compact_mode = False
        self._connect_feedback_clear_timer: Optional[QtCore.QTimer] = None
        self._auth_key_feedback_clear_timer: Optional[QtCore.QTimer] = None

        self.repo_root = Path(__file__).resolve().parent.parent
        self.app_root = self.repo_root / "app"
        self.main_py = self.app_root / "main.py"
        self.settings_path = Path.home() / ".hashwatcher-gateway-desktop" / "gui_settings.json"

        self.hostname_var = Var(DEFAULT_HOSTNAME)
        self.port_var = Var(DEFAULT_PORT)
        self.gateway_ip_var = Var("-")
        self.gateway_url_var = Var("-")
        self.status_var = Var("Gateway Stopped")
        self.header_tailscale_var = Var("Tailscale: Not connected")

        self.ts_tailnet_var = Var("")
        self.ts_api_key_var = Var("")
        self.ts_auth_key_var = Var("")
        self.ts_subnet_var = Var("")

        self.ts_state_var = Var("Tailscale: unknown")
        self.ts_ip_var = Var("Tailscale IP: -")
        self.ts_routes_var = Var("Advertised Routes: -")
        self.ts_route_approval_var = Var("Route Approval: -")
        self.route_approval_help_var = Var("Checking route approval status...")

        self.last_ts_status: Dict[str, Any] = {
            "gatewayReachable": False,
            "authenticated": False,
            "online": False,
            "routesApproved": False,
            "routesPending": False,
        }

        self.show_api_helper_var = BoolVar(False)
        self.show_route_images_var = BoolVar(False)
        self.dark_mode_var = BoolVar(True)

        self.wizard_step = 0
        self.wizard_complete = False
        self.wizard_steps = [
            {
                "title": "Step 1 of 5: Start Gateway",
                "body": "Start the local gateway service.",
                "action": "Start Gateway",
                "hint": "Gateway must be running before onboarding can continue.",
            },
            {
                "title": "Step 2 of 5: Add Auth Key",
                "body": "Paste your Tailscale auth key (tskey-auth-...) below.",
                "action": "Open Keys Page",
                "hint": "Do this before moving to Connect Gateway.",
            },
            {
                "title": "Step 3 of 5: Connect Gateway",
                "body": "Connect this gateway using the auth key you entered.",
                "action": "Connect Tailscale",
                "hint": "Optional subnet CIDR can be left blank for auto-detect.",
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
        self._setup_stylesheet()
        self._load_assets()
        self._build_layout()
        self._bind_variables()

        self._refresh_local_network_identity()
        self._render_wizard_step()
        self._apply_compact_layout()
        self._refresh_status()

        self.log_timer = QtCore.QTimer(self)
        self.log_timer.timeout.connect(self._drain_log_queue)
        self.log_timer.start(200)

        self.status_timer = QtCore.QTimer(self)
        self.status_timer.timeout.connect(self._refresh_status)
        self.status_timer.start(800)

        QtCore.QTimer.singleShot(1200, self.refresh_tailscale_status)

    # ----- setup -----
    def _setup_stylesheet(self) -> None:
        dark = True
        if dark:
            self.theme = {
                "feedback_info": "#A1A1AA",
                "feedback_ok": "#34D399",
                "feedback_warn": "#F59E0B",
                "feedback_bad": "#F87171",
                "state_good": "#34D399",
                "state_warn": "#FBBF24",
                "state_bad": "#F87171",
                "route_ok_bg": "#0F2A1F",
                "route_ok_fg": "#34D399",
                "route_warn_bg": "#2D230F",
                "route_warn_fg": "#FBBF24",
                "route_bad_bg": "#2F1618",
                "route_bad_fg": "#F87171",
                "route_info_bg": "#11263A",
                "route_info_fg": "#7CC2FF",
                "chip_info_bg": "#262A35",
                "chip_info_fg": "#E5E7EB",
                "chip_good_bg": "#0F2A1F",
                "chip_good_fg": "#34D399",
                "chip_warn_bg": "#2D230F",
                "chip_warn_fg": "#FBBF24",
                "chip_bad_bg": "#2F1618",
                "chip_bad_fg": "#F87171",
            }
            stylesheet = """
            QMainWindow { background: #111316; color: #E5E7EB; }
            QWidget { font-family: 'SF Pro Text', 'Segoe UI', sans-serif; color: #E5E7EB; }

            QFrame#HeroCard {
                background: #161A20;
                border: 1px solid #2A2F3A;
                border-radius: 22px;
            }

            QLabel#TitleLabel { font-size: 30px; font-weight: 700; color: #F3F4F6; }
            QLabel#SubtitleLabel { font-size: 13px; color: #A1A1AA; }

            QLabel[badge="true"] {
                border-radius: 12px;
                padding: 6px 11px;
                font-size: 12px;
                font-weight: 600;
                background: #262A35;
                color: #E5E7EB;
            }

            QTabWidget::pane { border: none; background: transparent; margin-top: 10px; }
            QScrollArea { border: none; background: transparent; }
            QScrollArea#RouteScrollArea > QWidget#qt_scrollarea_viewport { background: #111316; }
            QWidget#RouteScrollBody { background: transparent; }

            QTabBar::tab {
                background: #20242C;
                color: #A1A1AA;
                border: 1px solid #343B49;
                border-radius: 12px;
                padding: 8px 16px;
                margin-right: 8px;
                font-weight: 600;
                min-width: 120px;
            }

            QTabBar::tab:selected { background: #2A2F3A; color: #7CC2FF; border: 1px solid #4A5568; }

            QGroupBox {
                background: #161A20;
                border: 1px solid #2A2F3A;
                border-radius: 18px;
                margin-top: 1.4em;
                font-weight: 600;
                font-size: 14px;
                padding-top: 6px;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 14px;
                top: 0px;
                background: transparent;
                color: #E5E7EB;
                padding: 0 4px;
            }

            QLineEdit {
                background: #20242C;
                border: 1px solid #343B49;
                border-radius: 10px;
                padding: 8px 10px;
                min-height: 20px;
                color: #F3F4F6;
            }

            QLineEdit:disabled { background: #262A35; color: #C7CAD1; }

            QTextEdit {
                background: #131820;
                border: 1px solid #343B49;
                border-radius: 12px;
                padding: 8px;
                font-family: 'Menlo', 'Consolas', monospace;
                font-size: 12px;
                color: #D7DCE5;
            }

            QPushButton {
                border: none;
                border-radius: 14px;
                font-weight: 600;
                font-size: 13px;
                padding: 10px 16px;
                min-height: 24px;
            }

            QPushButton[role="primary"] { background: #147CE5; color: #FFFFFF; }
            QPushButton[role="primary"]:hover { background: #0D68C4; }
            QPushButton[role="primary"]:pressed { background: #0B539D; }
            QPushButton[role="primary"]:disabled { background: #36587E; color: #BFD5EA; }

            QPushButton[role="success"] { background: #16A34A; color: #FFFFFF; }
            QPushButton[role="success"]:hover { background: #15803D; }
            QPushButton[role="success"]:pressed { background: #166534; }
            QPushButton[role="success"]:disabled { background: #166534; color: #DFF7E8; }

            QPushButton[role="secondary"] {
                background: #262A35;
                color: #E5E7EB;
                border: 1px solid #343B49;
            }
            QPushButton[role="secondary"]:hover { background: #313746; }
            QPushButton[role="secondary"]:pressed { background: #3A4151; }
            QPushButton[role="secondary"]:disabled { background: #2B303D; color: #7C8392; }

            QPushButton[role="danger"] { background: #DC2626; color: #FFFFFF; }
            QPushButton[role="danger"]:hover { background: #B91C1C; }
            QPushButton[role="danger"]:pressed { background: #991B1B; }
            QPushButton[role="danger"]:disabled { background: #734040; color: #E6C8C8; }

            QLabel#WizardTitle { font-size: 18px; font-weight: 700; color: #F3F4F6; }
            QLabel#WizardBody { font-size: 15px; font-weight: 600; color: #E5E7EB; }
            QLabel#MetaText { color: #A1A1AA; font-size: 12px; }
            QLabel#StepHint { color: #7CC2FF; font-size: 12px; font-weight: 700; }

            QLabel#RouteBanner { border-radius: 12px; padding: 10px 12px; font-weight: 600; }
            QLabel#LinkLabel { color: #7CC2FF; font-size: 12px; }
            """
        else:
            self.theme = {
                "feedback_info": "#6E6E73",
                "feedback_ok": "#0A7F43",
                "feedback_warn": "#B54708",
                "feedback_bad": "#B42318",
                "state_good": "#0A7F43",
                "state_warn": "#B54708",
                "state_bad": "#B42318",
                "route_ok_bg": "#E7F4ED",
                "route_ok_fg": "#0A7F43",
                "route_warn_bg": "#FFF2E5",
                "route_warn_fg": "#B54708",
                "route_bad_bg": "#FDEDED",
                "route_bad_fg": "#B42318",
                "route_info_bg": "#EAF3FF",
                "route_info_fg": "#005BB5",
                "chip_info_bg": "#E9EDF5",
                "chip_info_fg": "#1D1D1F",
                "chip_good_bg": "#E7F4ED",
                "chip_good_fg": "#0A7F43",
                "chip_warn_bg": "#FFF2E5",
                "chip_warn_fg": "#B54708",
                "chip_bad_bg": "#FDEDED",
                "chip_bad_fg": "#B42318",
            }
            stylesheet = """
            QMainWindow { background: #F5F5F7; color: #1D1D1F; }
            QWidget { font-family: 'SF Pro Text', 'Segoe UI', sans-serif; color: #1D1D1F; }

            QFrame#HeroCard {
                background: #FFFFFF;
                border: 1px solid #E3E5EA;
                border-radius: 22px;
            }

            QLabel#TitleLabel { font-size: 30px; font-weight: 700; color: #1D1D1F; }
            QLabel#SubtitleLabel { font-size: 13px; color: #6E6E73; }

            QLabel[badge="true"] {
                border-radius: 12px;
                padding: 6px 11px;
                font-size: 12px;
                font-weight: 600;
                background: #E9EDF5;
                color: #1D1D1F;
            }

            QTabWidget::pane { border: none; background: transparent; margin-top: 10px; }
            QScrollArea { border: none; background: transparent; }
            QScrollArea#RouteScrollArea > QWidget#qt_scrollarea_viewport { background: #F5F5F7; }
            QWidget#RouteScrollBody { background: transparent; }

            QTabBar::tab {
                background: #EDEEF2;
                color: #6E6E73;
                border: 1px solid #D5D9E2;
                border-radius: 12px;
                padding: 8px 16px;
                margin-right: 8px;
                font-weight: 600;
                min-width: 120px;
            }

            QTabBar::tab:selected { background: #FFFFFF; color: #0071E3; border: 1px solid #CDD5E1; }

            QGroupBox {
                background: #FFFFFF;
                border: 1px solid #D2D2D7;
                border-radius: 18px;
                margin-top: 1.4em;
                font-weight: 600;
                font-size: 14px;
                padding-top: 6px;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 14px;
                top: 0px;
                background: transparent;
                color: #1D1D1F;
                padding: 0 4px;
            }

            QLineEdit {
                background: #FAFAFC;
                border: 1px solid #D2D2D7;
                border-radius: 10px;
                padding: 8px 10px;
                min-height: 20px;
            }

            QLineEdit:disabled { background: #E9EDF5; color: #3A3A3C; }

            QTextEdit {
                background: #FBFBFD;
                border: 1px solid #D2D2D7;
                border-radius: 12px;
                padding: 8px;
                font-family: 'Menlo', 'Consolas', monospace;
                font-size: 12px;
                color: #1F2330;
            }

            QPushButton {
                border: none;
                border-radius: 14px;
                font-weight: 600;
                font-size: 13px;
                padding: 10px 16px;
                min-height: 24px;
            }

            QPushButton[role="primary"] { background: #0071E3; color: #FFFFFF; }
            QPushButton[role="primary"]:hover { background: #005BB5; }
            QPushButton[role="primary"]:pressed { background: #004A94; }
            QPushButton[role="primary"]:disabled { background: #9CB8DA; color: #EFF4FB; }

            QPushButton[role="success"] { background: #16A34A; color: #FFFFFF; }
            QPushButton[role="success"]:hover { background: #15803D; }
            QPushButton[role="success"]:pressed { background: #166534; }
            QPushButton[role="success"]:disabled { background: #15803D; color: #EAF8F0; }

            QPushButton[role="secondary"] {
                background: #E9EDF5;
                color: #1D1D1F;
                border: 1px solid #D0D8E4;
            }
            QPushButton[role="secondary"]:hover { background: #DCE2EC; }
            QPushButton[role="secondary"]:pressed { background: #D2DAE6; }
            QPushButton[role="secondary"]:disabled { background: #D7DCE5; color: #9BA3AF; }

            QPushButton[role="danger"] { background: #D92D20; color: #FFFFFF; }
            QPushButton[role="danger"]:hover { background: #B42318; }
            QPushButton[role="danger"]:pressed { background: #9E1C12; }
            QPushButton[role="danger"]:disabled { background: #D9A3A0; color: #F8EEEE; }

            QLabel#WizardTitle { font-size: 18px; font-weight: 700; color: #1D1D1F; }
            QLabel#WizardBody { font-size: 15px; font-weight: 600; color: #1D1D1F; }
            QLabel#MetaText { color: #6E6E73; font-size: 12px; }
            QLabel#StepHint { color: #0071E3; font-size: 12px; font-weight: 700; }

            QLabel#RouteBanner { border-radius: 12px; padding: 10px 12px; font-weight: 600; }
            QLabel#LinkLabel { color: #0071E3; font-size: 12px; }
            """
        self.setStyleSheet(stylesheet)

    def _set_button_role(self, button: QtWidgets.QPushButton, role: str) -> None:
        button.setProperty("role", role)
        button.style().unpolish(button)
        button.style().polish(button)

    def _load_assets(self) -> None:
        self.logo_pixmap: Optional[QtGui.QPixmap] = None
        self.route_pixmaps: Dict[str, QtGui.QPixmap] = {}
        self.app_icon: Optional[QtGui.QIcon] = None
        self.icon_source_pixmap: Optional[QtGui.QPixmap] = None

        icon_path = self.app_root / "gateway" / "assets" / "icon.png"
        if icon_path.exists():
            pm = QtGui.QPixmap(str(icon_path))
            if not pm.isNull():
                self.icon_source_pixmap = pm
                self.logo_pixmap = self._rounded_icon_pixmap(pm, 52, 12.0)
                self.app_icon = QtGui.QIcon()
                for size in (16, 24, 32, 48, 64, 96, 128, 256, 512):
                    self.app_icon.addPixmap(self._rounded_icon_pixmap(pm, size, float(size) * 0.22))
                self.setWindowIcon(self.app_icon)
                app = QtWidgets.QApplication.instance()
                if app is not None:
                    app.setWindowIcon(self.app_icon)

        assets = self.app_root / "gateway" / "assets"
        for key in ("step4a", "step4b", "step4c"):
            path = assets / f"{key}.png"
            if not path.exists():
                continue
            pm = QtGui.QPixmap(str(path))
            if pm.isNull():
                continue
            self.route_pixmaps[key] = pm

    def _rounded_icon_pixmap(self, src: QtGui.QPixmap, size: int, radius: float) -> QtGui.QPixmap:
        scaled = src.scaled(
            size,
            size,
            QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        if scaled.width() != size or scaled.height() != size:
            x = max(0, (scaled.width() - size) // 2)
            y = max(0, (scaled.height() - size) // 2)
            scaled = scaled.copy(x, y, size, size)

        rounded = QtGui.QPixmap(size, size)
        rounded.fill(QtCore.Qt.GlobalColor.transparent)

        painter = QtGui.QPainter(rounded)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)

        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(0.0, 0.0, float(size), float(size)), radius, radius)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, scaled)
        painter.end()
        return rounded

    def _build_layout(self) -> None:
        root = QtWidgets.QWidget(self)
        self.setCentralWidget(root)
        root_layout = QtWidgets.QVBoxLayout(root)
        root_layout.setContentsMargins(20, 18, 20, 18)
        root_layout.setSpacing(12)

        self.hero_card = QtWidgets.QFrame()
        self.hero_card.setObjectName("HeroCard")
        hero_layout = QtWidgets.QHBoxLayout(self.hero_card)
        hero_layout.setContentsMargins(18, 16, 18, 16)
        hero_layout.setSpacing(14)

        if self.logo_pixmap is not None:
            logo = QtWidgets.QLabel()
            logo.setPixmap(self.logo_pixmap)
            logo.setFixedSize(54, 54)
            logo.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            hero_layout.addWidget(logo, 0, QtCore.Qt.AlignmentFlag.AlignTop)

        text_col = QtWidgets.QVBoxLayout()
        text_col.setSpacing(6)

        self.header_title_label = QtWidgets.QLabel("HashWatcher Gateway Desktop")
        self.header_title_label.setObjectName("TitleLabel")
        text_col.addWidget(self.header_title_label)

        chip_row = QtWidgets.QHBoxLayout()
        chip_row.setSpacing(8)
        self.header_gateway_chip = QtWidgets.QLabel()
        self.header_gateway_chip.setProperty("badge", True)
        self.header_tailscale_chip = QtWidgets.QLabel()
        self.header_tailscale_chip.setProperty("badge", True)
        chip_row.addWidget(self.header_gateway_chip)
        chip_row.addWidget(self.header_tailscale_chip)
        chip_row.addStretch(1)
        text_col.addLayout(chip_row)

        self.header_note_label = QtWidgets.QLabel(
            "Required: Keep this window open to allow remote access from the HashWatcher App."
        )
        self.header_note_label.setObjectName("SubtitleLabel")
        self.header_note_label.setWordWrap(True)
        text_col.addWidget(self.header_note_label)

        self.header_link_row = QtWidgets.QHBoxLayout()
        self.header_link_row.setSpacing(14)
        self.header_link_row.addWidget(self._link_label("www.HashWatcher.app", "https://www.HashWatcher.app"))
        self.header_link_row.addWidget(self._link_label("x.com/HashWatcher", "https://x.com/HashWatcher"))
        self.header_link_row.addStretch(1)
        text_col.addLayout(self.header_link_row)

        hero_layout.addLayout(text_col, 1)
        root_layout.addWidget(self.hero_card)

        self.tabs = QtWidgets.QTabWidget()
        root_layout.addWidget(self.tabs, 1)

        self.gateway_tab = QtWidgets.QWidget()
        self.onboarding_tab = QtWidgets.QWidget()
        self.tabs.addTab(self.gateway_tab, "Gateway")
        self.tabs.addTab(self.onboarding_tab, "Guided Onboarding")
        self.tabs.setCurrentWidget(self.onboarding_tab)

        self._build_gateway_tab(self.gateway_tab)
        self._build_onboarding_tab(self.onboarding_tab)

    def _link_label(self, text: str, url: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(f'<a href="{url}">{text}</a>')
        label.setObjectName("LinkLabel")
        label.setOpenExternalLinks(True)
        label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        return label

    def _bind_variables(self) -> None:
        self.status_var.bind(self.header_gateway_chip.setText)
        self.header_tailscale_var.bind(self.header_tailscale_chip.setText)
        self.status_var.bind(self.complete_gateway_label.setText)

        self.hostname_var.bind(lambda v: self._sync_line_edit(self.hostname_input, v))
        self.port_var.bind(lambda v: self._sync_line_edit(self.port_input, v))
        self.gateway_ip_var.bind(lambda v: self._sync_line_edit(self.gateway_ip_input, v))
        self.gateway_url_var.bind(lambda v: self._sync_line_edit(self.dashboard_url_input, v))

        self.ts_auth_key_var.bind(lambda v: self._sync_line_edit(self.auth_key_input, v))
        self.ts_subnet_var.bind(lambda v: self._sync_line_edit(self.subnet_input, v))
        self.ts_tailnet_var.bind(lambda v: self._sync_line_edit(self.tailnet_input, v))
        self.ts_api_key_var.bind(lambda v: self._sync_line_edit(self.api_key_input, v))

        self.ts_state_var.bind(self.wizard_step_state_label.setText)
        self.ts_state_var.bind(self.ts_state_verify_label.setText)
        self.ts_state_var.bind(self.complete_tailscale_state_label.setText)
        self.ts_ip_var.bind(self.wizard_step_ip_label.setText)
        self.ts_ip_var.bind(self.ts_ip_verify_label.setText)
        self.ts_ip_var.bind(self.complete_tailscale_ip_label.setText)
        self.ts_routes_var.bind(self.ts_routes_label.setText)
        self.ts_routes_var.bind(self.ts_routes_verify_label.setText)
        self.ts_routes_var.bind(self.complete_tailscale_routes_label.setText)
        self.ts_route_approval_var.bind(self.ts_route_approval_label.setText)
        self.ts_route_approval_var.bind(self.ts_route_approval_verify_label.setText)
        self.ts_route_approval_var.bind(self.complete_route_approval_label.setText)
        self.route_approval_help_var.bind(self.route_approval_help_label.setText)

        self.wizard_title_var.bind(self.wizard_title_label.setText)
        self.wizard_body_var.bind(self.wizard_body_label.setText)
        self.wizard_hint_var.bind(self.wizard_hint_label.setText)
        self.wizard_progress_var.bind(self.wizard_progress_label.setText)

        self.connect_feedback_var.bind(self.connect_feedback_label.setText)

    def _sync_line_edit(self, edit: QtWidgets.QLineEdit, value: str) -> None:
        if edit.text() != value:
            edit.blockSignals(True)
            edit.setText(value)
            edit.blockSignals(False)

    # ----- tab builders -----
    def _build_gateway_tab(self, tab: QtWidgets.QWidget) -> None:
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        controls_group = QtWidgets.QGroupBox("Gateway Controls")
        controls_layout = QtWidgets.QGridLayout(controls_group)
        controls_layout.setContentsMargins(14, 16, 14, 14)
        controls_layout.setHorizontalSpacing(10)
        controls_layout.setVerticalSpacing(10)

        self.machine_name_label = QtWidgets.QLabel("Machine Name")
        self.hostname_input = QtWidgets.QLineEdit()
        self.hostname_input.textChanged.connect(self.hostname_var.set)

        self.gateway_ip_label = QtWidgets.QLabel("Gateway IP")
        self.gateway_ip_input = QtWidgets.QLineEdit()
        self.gateway_ip_input.setReadOnly(True)
        self.gateway_ip_input.setDisabled(True)

        self.port_label = QtWidgets.QLabel("Port")
        self.port_input = QtWidgets.QLineEdit()
        self.port_input.setMaximumWidth(110)
        self.port_input.textChanged.connect(self.port_var.set)

        self.dashboard_url_label = QtWidgets.QLabel("Dashboard URL")
        self.dashboard_url_input = QtWidgets.QLineEdit()
        self.dashboard_url_input.setReadOnly(True)
        self.dashboard_url_input.setDisabled(True)

        controls_layout.addWidget(self.machine_name_label, 0, 0)
        controls_layout.addWidget(self.hostname_input, 0, 1)
        controls_layout.addWidget(self.gateway_ip_label, 0, 2)
        controls_layout.addWidget(self.gateway_ip_input, 0, 3)
        controls_layout.addWidget(self.port_label, 1, 0)
        controls_layout.addWidget(self.port_input, 1, 1)
        controls_layout.addWidget(self.dashboard_url_label, 1, 2)
        controls_layout.addWidget(self.dashboard_url_input, 1, 3)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(8)

        self.start_btn = QtWidgets.QPushButton("Start Gateway")
        self._set_button_role(self.start_btn, "primary")
        self.start_btn.clicked.connect(self.start_gateway)

        self.stop_btn = QtWidgets.QPushButton("Stop Gateway")
        self._set_button_role(self.stop_btn, "danger")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_gateway)

        self.refresh_status_btn = QtWidgets.QPushButton("Refresh Status")
        self._set_button_role(self.refresh_status_btn, "secondary")
        self.refresh_status_btn.clicked.connect(self.refresh_tailscale_status)

        self.disconnect_btn = QtWidgets.QPushButton("Disconnect Tailscale")
        self._set_button_role(self.disconnect_btn, "danger")
        self.disconnect_btn.clicked.connect(self.disconnect_tailscale)

        button_row.addWidget(self.start_btn)
        button_row.addWidget(self.stop_btn)
        button_row.addWidget(self.refresh_status_btn)
        button_row.addWidget(self.disconnect_btn)
        button_row.addStretch(1)

        controls_layout.addLayout(button_row, 2, 0, 1, 4)
        controls_layout.setColumnStretch(1, 1)
        controls_layout.setColumnStretch(3, 1)

        logs_group = QtWidgets.QGroupBox("Gateway Logs")
        logs_layout = QtWidgets.QVBoxLayout(logs_group)
        logs_layout.setContentsMargins(12, 16, 12, 12)
        self.log_text = QtWidgets.QTextEdit()
        self.log_text.setReadOnly(True)
        logs_layout.addWidget(self.log_text)

        layout.addWidget(controls_group)
        layout.addWidget(logs_group, 1)

    def _build_onboarding_tab(self, tab: QtWidgets.QWidget) -> None:
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        self.wizard_group = QtWidgets.QGroupBox("Setup Wizard")
        wizard_layout = QtWidgets.QGridLayout(self.wizard_group)
        wizard_layout.setContentsMargins(14, 18, 14, 14)
        wizard_layout.setHorizontalSpacing(8)
        wizard_layout.setVerticalSpacing(8)

        self.wizard_title_var = Var("")
        self.wizard_body_var = Var("")
        self.wizard_hint_var = Var("")
        self.wizard_progress_var = Var("")

        self.wizard_title_label = QtWidgets.QLabel()
        self.wizard_title_label.setObjectName("WizardTitle")
        self.wizard_title_label.setWordWrap(True)

        next_label = QtWidgets.QLabel("NEXT STEP")
        next_label.setObjectName("StepHint")

        self.wizard_body_label = QtWidgets.QLabel()
        self.wizard_body_label.setObjectName("WizardBody")
        self.wizard_body_label.setWordWrap(True)

        self.wizard_hint_label = QtWidgets.QLabel()
        self.wizard_hint_label.setObjectName("MetaText")
        self.wizard_hint_label.setWordWrap(True)

        self.wizard_step_state_label = QtWidgets.QLabel("")
        self.wizard_step_state_label.setObjectName("WizardBody")
        self.wizard_step_state_label.setVisible(False)

        self.wizard_step_ip_label = QtWidgets.QLabel("")
        self.wizard_step_ip_label.setObjectName("MetaText")
        self.wizard_step_ip_label.setVisible(False)

        self.wizard_progress_label = QtWidgets.QLabel()
        self.wizard_progress_label.setObjectName("MetaText")

        self.wizard_action_btn = QtWidgets.QPushButton()
        self._set_button_role(self.wizard_action_btn, "primary")
        self.wizard_action_btn.clicked.connect(self._run_wizard_action)

        self.wizard_back_btn = QtWidgets.QPushButton("Back")
        self._set_button_role(self.wizard_back_btn, "secondary")
        self.wizard_back_btn.clicked.connect(self._wizard_back)

        self.wizard_next_btn = QtWidgets.QPushButton("Next")
        self._set_button_role(self.wizard_next_btn, "primary")
        self.wizard_next_btn.clicked.connect(self._wizard_next)

        wizard_layout.addWidget(self.wizard_title_label, 0, 0, 1, 4)
        wizard_layout.addWidget(next_label, 1, 0, 1, 4)
        wizard_layout.addWidget(self.wizard_body_label, 2, 0, 1, 4)
        wizard_layout.addWidget(self.wizard_hint_label, 3, 0, 1, 4)
        wizard_layout.addWidget(self.wizard_step_state_label, 4, 0, 1, 4)
        wizard_layout.addWidget(self.wizard_step_ip_label, 5, 0, 1, 4)
        wizard_layout.addWidget(self.wizard_progress_label, 6, 0, 1, 4)
        wizard_layout.addWidget(self.wizard_action_btn, 7, 0)
        wizard_layout.setColumnStretch(1, 1)
        wizard_layout.addWidget(self.wizard_back_btn, 7, 2)
        wizard_layout.addWidget(self.wizard_next_btn, 7, 3)

        self._tailscale_state_labels = [self.wizard_step_state_label]

        layout.addWidget(self.wizard_group)

        self.step_stack = QtWidgets.QStackedWidget()
        layout.addWidget(self.step_stack, 1)

        self.step_stack.addWidget(self._build_step_start())
        self.step_stack.addWidget(self._build_step_auth_key())
        self.step_stack.addWidget(self._build_step_connect())
        self.step_stack.addWidget(self._build_step_route_approval())
        self.step_stack.addWidget(self._build_step_verify())
        self.step_stack.addWidget(self._build_step_complete())
        self.completion_page_index = self.step_stack.count() - 1

    def _step_card(self, title: str) -> tuple[QtWidgets.QWidget, QtWidgets.QVBoxLayout]:
        panel = QtWidgets.QWidget()
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        card = QtWidgets.QGroupBox(title)
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(14, 18, 14, 14)
        card_layout.setSpacing(10)
        panel_layout.addWidget(card)
        panel_layout.addStretch(1)
        return panel, card_layout

    def _build_step_start(self) -> QtWidgets.QWidget:
        panel, card = self._step_card("Start Gateway")

        intro = QtWidgets.QLabel("Use the default machine name HashWatcherGatewayDesktop unless you have a specific reason to change it.")
        intro.setWordWrap(True)
        intro.setStyleSheet("font-weight: 600;")

        prereq = QtWidgets.QLabel("Prereqs: install the HashWatcher app first, and keep this gateway app running for best reliability.")
        prereq.setWordWrap(True)
        prereq.setObjectName("MetaText")

        links = QtWidgets.QHBoxLayout()
        links.addWidget(self._link_label("Open www.HashWatcher.app", "https://www.HashWatcher.app"))
        links.addWidget(self._link_label("Open x.com/HashWatcher", "https://x.com/HashWatcher"))
        links.addStretch(1)

        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        step_machine_input = QtWidgets.QLineEdit()
        step_machine_input.textChanged.connect(self.hostname_var.set)
        self.hostname_var.bind(lambda v: self._sync_line_edit(step_machine_input, v))

        step_port_input = QtWidgets.QLineEdit()
        step_port_input.setMaximumWidth(110)
        step_port_input.textChanged.connect(self.port_var.set)
        self.port_var.bind(lambda v: self._sync_line_edit(step_port_input, v))

        form.addWidget(QtWidgets.QLabel("Machine Name"), 0, 0)
        form.addWidget(step_machine_input, 0, 1)
        form.addWidget(QtWidgets.QLabel("Port"), 0, 2)
        form.addWidget(step_port_input, 0, 3)
        form.setColumnStretch(1, 1)

        actions = QtWidgets.QHBoxLayout()
        self.step_start_btn = QtWidgets.QPushButton("Start Gateway")
        self._set_button_role(self.step_start_btn, "primary")
        self.step_start_btn.clicked.connect(self.start_gateway)
        actions.addWidget(self.step_start_btn)

        self.step_gateway_status_chip = QtWidgets.QLabel()
        self.step_gateway_status_chip.setProperty("badge", True)
        self.status_var.bind(self.step_gateway_status_chip.setText)
        actions.addWidget(self.step_gateway_status_chip)
        actions.addStretch(1)

        card.addWidget(intro)
        card.addWidget(prereq)
        card.addLayout(links)
        card.addLayout(form)
        card.addLayout(actions)
        return panel

    def _build_step_auth_key(self) -> QtWidgets.QWidget:
        panel, card = self._step_card("Add Tailscale Auth Key (Required)")

        intro = QtWidgets.QLabel("1) Open the Tailscale Keys page. 2) Create an auth key. 3) Paste it here.")
        intro.setWordWrap(True)
        intro.setStyleSheet("font-weight: 600;")
        card.addWidget(intro)

        card.addWidget(QtWidgets.QLabel("Auth Key (tskey-auth-...)"))
        self.auth_key_input = QtWidgets.QLineEdit()
        self.auth_key_input.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.auth_key_input.textChanged.connect(self.ts_auth_key_var.set)
        self.auth_key_input.textChanged.connect(lambda _: self._set_auth_key_feedback("", timeout_ms=0))
        card.addWidget(self.auth_key_input)

        links = QtWidgets.QHBoxLayout()
        keys_btn = QtWidgets.QPushButton("Open Keys Page")
        self._set_button_role(keys_btn, "primary")
        keys_btn.clicked.connect(self.open_tailscale_keys)

        valid_btn = QtWidgets.QPushButton("Validate Key")
        self._set_button_role(valid_btn, "secondary")
        valid_btn.clicked.connect(self._validate_auth_key_format)

        self.auth_key_feedback_label = QtWidgets.QLabel("")
        self.auth_key_feedback_label.setObjectName("MetaText")

        links.addWidget(keys_btn)
        links.addWidget(valid_btn)
        links.addWidget(self.auth_key_feedback_label)
        links.addStretch(1)
        card.addLayout(links)

        self.api_helper_toggle = QtWidgets.QCheckBox("Show optional API helper")
        self.api_helper_toggle.toggled.connect(self.show_api_helper_var.set)
        card.addWidget(self.api_helper_toggle)

        self.api_helper_frame = QtWidgets.QFrame()
        api_helper_layout = QtWidgets.QVBoxLayout(self.api_helper_frame)
        api_helper_layout.setContentsMargins(0, 0, 0, 0)
        api_helper_layout.setSpacing(8)

        api_helper_layout.addWidget(QtWidgets.QLabel("Tailnet (example: your-tailnet or example.com)"))
        self.tailnet_input = QtWidgets.QLineEdit()
        self.tailnet_input.textChanged.connect(self.ts_tailnet_var.set)
        api_helper_layout.addWidget(self.tailnet_input)

        api_helper_layout.addWidget(QtWidgets.QLabel("Tailscale API Key"))
        self.api_key_input = QtWidgets.QLineEdit()
        self.api_key_input.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.api_key_input.textChanged.connect(self.ts_api_key_var.set)
        api_helper_layout.addWidget(self.api_key_input)

        gen_btn = QtWidgets.QPushButton("Generate Auth Key via API")
        self._set_button_role(gen_btn, "secondary")
        gen_btn.clicked.connect(self.generate_auth_key_via_api)
        api_helper_layout.addWidget(gen_btn, 0, QtCore.Qt.AlignmentFlag.AlignLeft)

        card.addWidget(self.api_helper_frame)
        self.show_api_helper_var.bind(lambda show: self.api_helper_frame.setVisible(show))
        return panel

    def _build_step_connect(self) -> QtWidgets.QWidget:
        panel, card = self._step_card("Connect Gateway")

        intro = QtWidgets.QLabel("Use your auth key to connect this machine to your tailnet. Subnet can stay blank for auto-detect.")
        intro.setWordWrap(True)
        intro.setStyleSheet("font-weight: 600;")
        card.addWidget(intro)

        self.connect_controls_toggle_btn = QtWidgets.QPushButton("Expand Connection Controls")
        self._set_button_role(self.connect_controls_toggle_btn, "secondary")
        self.connect_controls_toggle_btn.setCheckable(True)
        self.connect_controls_toggle_btn.toggled.connect(self._set_connect_controls_visible)
        card.addWidget(self.connect_controls_toggle_btn, 0, QtCore.Qt.AlignmentFlag.AlignLeft)

        self.connect_controls_frame = QtWidgets.QFrame()
        controls_layout = QtWidgets.QVBoxLayout(self.connect_controls_frame)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)

        self.subnet_expand_btn = QtWidgets.QPushButton("Expand Optional Subnet")
        self._set_button_role(self.subnet_expand_btn, "secondary")
        self.subnet_expand_btn.setCheckable(True)
        self.subnet_expand_btn.toggled.connect(self._set_subnet_advanced_visible)
        controls_layout.addWidget(self.subnet_expand_btn, 0, QtCore.Qt.AlignmentFlag.AlignLeft)

        self.subnet_advanced_frame = QtWidgets.QFrame()
        subnet_layout = QtWidgets.QVBoxLayout(self.subnet_advanced_frame)
        subnet_layout.setContentsMargins(0, 0, 0, 0)
        subnet_layout.setSpacing(6)
        subnet_layout.addWidget(QtWidgets.QLabel("Subnet CIDR (optional, example: 192.168.1.0/24)"))

        self.subnet_input = QtWidgets.QLineEdit()
        self.subnet_input.setMaximumWidth(260)
        self.subnet_input.textChanged.connect(self.ts_subnet_var.set)
        subnet_layout.addWidget(self.subnet_input, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        controls_layout.addWidget(self.subnet_advanced_frame)
        self._set_subnet_advanced_visible(False)

        buttons = QtWidgets.QHBoxLayout()

        connect_btn = QtWidgets.QPushButton("Connect")
        self._set_button_role(connect_btn, "primary")
        connect_btn.clicked.connect(self.connect_tailscale)

        refresh_btn = QtWidgets.QPushButton("Refresh Status")
        self._set_button_role(refresh_btn, "secondary")
        refresh_btn.clicked.connect(self.refresh_tailscale_status)

        disc_btn = QtWidgets.QPushButton("Disconnect Tailscale")
        self._set_button_role(disc_btn, "danger")
        disc_btn.clicked.connect(self.disconnect_tailscale)

        self.connect_feedback_var = Var("")
        self.connect_feedback_label = QtWidgets.QLabel("")
        self.connect_feedback_label.setObjectName("MetaText")

        buttons.addWidget(connect_btn)
        buttons.addWidget(refresh_btn)
        buttons.addWidget(disc_btn)
        buttons.addWidget(self.connect_feedback_label)
        buttons.addStretch(1)

        controls_layout.addLayout(buttons)
        card.addWidget(self.connect_controls_frame)
        self._set_connect_controls_visible(False)
        return panel

    def _build_step_route_approval(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        self.route_scroll_area = QtWidgets.QScrollArea()
        self.route_scroll_area.setObjectName("RouteScrollArea")
        self.route_scroll_area.setWidgetResizable(True)
        self.route_scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.route_scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.route_scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        scroll_body = QtWidgets.QWidget()
        scroll_body.setObjectName("RouteScrollBody")
        scroll_layout = QtWidgets.QVBoxLayout(scroll_body)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(0)

        card = QtWidgets.QGroupBox("Approve Route")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(14, 18, 14, 14)
        card_layout.setSpacing(10)
        scroll_layout.addWidget(card)
        scroll_layout.addStretch(1)

        self.route_scroll_area.setWidget(scroll_body)
        panel_layout.addWidget(self.route_scroll_area)

        intro = QtWidgets.QLabel("Open the Tailscale Machines page and approve the subnet route for HashWatcherGatewayDesktop.")
        intro.setWordWrap(True)
        intro.setStyleSheet("font-weight: 600;")
        card_layout.addWidget(intro)

        self.route_status_banner = QtWidgets.QLabel("Route Approval: Checking...")
        self.route_status_banner.setObjectName("RouteBanner")
        card_layout.addWidget(self.route_status_banner)

        self.route_approval_help_label = QtWidgets.QLabel()
        self.route_approval_help_label.setWordWrap(True)
        self.route_approval_help_label.setObjectName("MetaText")
        card_layout.addWidget(self.route_approval_help_label)

        buttons = QtWidgets.QHBoxLayout()

        open_btn = QtWidgets.QPushButton("Open Machines Page")
        self._set_button_role(open_btn, "primary")
        open_btn.clicked.connect(self.open_tailscale_machines)

        refresh_btn = QtWidgets.QPushButton("Refresh Status")
        self._set_button_role(refresh_btn, "secondary")
        refresh_btn.clicked.connect(self.refresh_tailscale_status)

        self.route_guide_btn = QtWidgets.QPushButton("Show Me How")
        self._set_button_role(self.route_guide_btn, "secondary")
        self.route_guide_btn.clicked.connect(self._toggle_route_images)

        buttons.addWidget(open_btn)
        buttons.addWidget(refresh_btn)
        buttons.addWidget(self.route_guide_btn)
        buttons.addStretch(1)
        card_layout.addLayout(buttons)

        self.route_flow_label = QtWidgets.QLabel(
            "Steps: Open HashWatcherGatewayDesktop in Machines, click ... then Edit route settings, approve the subnet route, then press Refresh Status here."
        )
        self.route_flow_label.setWordWrap(True)
        self.route_flow_label.setObjectName("MetaText")
        card_layout.addWidget(self.route_flow_label)

        self.route_admin_link_label = self._link_label(
            "Approve subnet now in Tailscale Admin",
            "https://login.tailscale.com/admin/machines",
        )
        card_layout.addWidget(self.route_admin_link_label)

        self.ts_routes_label = QtWidgets.QLabel()
        self.ts_routes_label.setStyleSheet("font-weight: 600;")
        self.ts_route_approval_label = QtWidgets.QLabel()
        self.ts_route_approval_label.setStyleSheet("font-weight: 600;")
        card_layout.addWidget(self.ts_routes_label)
        card_layout.addWidget(self.ts_route_approval_label)

        self.route_images_frame = QtWidgets.QFrame()
        image_layout = QtWidgets.QVBoxLayout(self.route_images_frame)
        image_layout.setContentsMargins(0, 2, 0, 0)
        image_layout.setSpacing(8)

        self.route_guide_steps: list[tuple[str, str]] = [
            ("step4a", "1) Find HashWatcherGatewayDesktop in Machines"),
            ("step4b", "2) Click ... and choose Edit route settings"),
            ("step4c", "3) Approve the advertised subnet route"),
        ]
        self.route_guide_index = 0

        self.route_image_title_label = QtWidgets.QLabel()
        self.route_image_title_label.setObjectName("WizardBody")
        self.route_image_title_label.setWordWrap(True)
        image_layout.addWidget(self.route_image_title_label)

        self.route_image_display = QtWidgets.QLabel()
        self.route_image_display.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.route_image_display.setMinimumHeight(180)
        self.route_image_display.setStyleSheet("border:1px solid #2F3748; border-radius:12px; padding:8px;")
        image_layout.addWidget(self.route_image_display)

        nav = QtWidgets.QHBoxLayout()
        nav.setSpacing(8)
        self.route_prev_btn = QtWidgets.QPushButton("Back")
        self._set_button_role(self.route_prev_btn, "secondary")
        self.route_prev_btn.clicked.connect(lambda: self._change_route_guide_image(-1))

        self.route_next_btn = QtWidgets.QPushButton("Next")
        self._set_button_role(self.route_next_btn, "secondary")
        self.route_next_btn.clicked.connect(lambda: self._change_route_guide_image(1))

        self.route_page_label = QtWidgets.QLabel("")
        self.route_page_label.setObjectName("MetaText")

        nav.addWidget(self.route_prev_btn)
        nav.addWidget(self.route_next_btn)
        nav.addWidget(self.route_page_label)
        nav.addStretch(1)
        image_layout.addLayout(nav)

        card_layout.addWidget(self.route_images_frame)
        self._render_route_guide_image()
        self._apply_route_guide_visibility()
        return panel

    def _build_step_verify(self) -> QtWidgets.QWidget:
        panel, card = self._step_card("Verify Setup")

        intro = QtWidgets.QLabel("Setup is complete when state is online/authenticated and route approval shows approved.")
        intro.setWordWrap(True)
        intro.setStyleSheet("font-weight: 600;")
        card.addWidget(intro)

        refresh_btn = QtWidgets.QPushButton("Refresh Status")
        self._set_button_role(refresh_btn, "primary")
        refresh_btn.clicked.connect(self.refresh_tailscale_status)
        card.addWidget(refresh_btn, 0, QtCore.Qt.AlignmentFlag.AlignLeft)

        self.ts_state_verify_label = QtWidgets.QLabel()
        self.ts_state_verify_label.setStyleSheet("font-weight: 600;")
        self.ts_ip_verify_label = QtWidgets.QLabel(); self.ts_ip_verify_label.setStyleSheet("font-weight: 600;")
        self.ts_routes_verify_label = QtWidgets.QLabel(); self.ts_routes_verify_label.setStyleSheet("font-weight: 600;")
        self.ts_route_approval_verify_label = QtWidgets.QLabel(); self.ts_route_approval_verify_label.setStyleSheet("font-weight: 600;")

        card.addWidget(self.ts_state_verify_label)
        card.addWidget(self.ts_ip_verify_label)
        card.addWidget(self.ts_routes_verify_label)
        card.addWidget(self.ts_route_approval_verify_label)

        self._tailscale_state_labels.append(self.ts_state_verify_label)
        return panel

    def _build_step_complete(self) -> QtWidgets.QWidget:
        panel, card = self._step_card("HashWatcher Live")

        self.complete_logo_label = QtWidgets.QLabel()
        self.complete_logo_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.complete_logo_label.setMinimumHeight(132)
        if self.icon_source_pixmap is not None:
            self.complete_logo_label.setPixmap(self._rounded_icon_pixmap(self.icon_source_pixmap, 116, 26.0))
        elif self.logo_pixmap is not None:
            self.complete_logo_label.setPixmap(self.logo_pixmap)
        card.addWidget(self.complete_logo_label, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)

        title = QtWidgets.QLabel("May the Block be with you.")
        title.setObjectName("TitleLabel")
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        card.addWidget(title)

        subtitle = QtWidgets.QLabel("Gateway onboarding is complete. Remote access is live in HashWatcher.")
        subtitle.setWordWrap(True)
        subtitle.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        subtitle.setObjectName("WizardBody")
        card.addWidget(subtitle)

        self.complete_gateway_label = QtWidgets.QLabel()
        self.complete_gateway_label.setProperty("badge", True)
        self.complete_tailscale_state_label = QtWidgets.QLabel()
        self.complete_tailscale_state_label.setProperty("badge", True)
        self.complete_tailscale_ip_label = QtWidgets.QLabel()
        self.complete_tailscale_routes_label = QtWidgets.QLabel()
        self.complete_route_approval_label = QtWidgets.QLabel()

        chip_row = QtWidgets.QHBoxLayout()
        chip_row.addStretch(1)
        chip_row.addWidget(self.complete_gateway_label)
        chip_row.addWidget(self.complete_tailscale_state_label)
        chip_row.addStretch(1)
        card.addLayout(chip_row)

        for label in (self.complete_tailscale_ip_label, self.complete_tailscale_routes_label, self.complete_route_approval_label):
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            label.setObjectName("WizardBody")
            card.addWidget(label)

        actions = QtWidgets.QHBoxLayout()
        refresh_btn = QtWidgets.QPushButton("Refresh Status")
        self._set_button_role(refresh_btn, "primary")
        refresh_btn.clicked.connect(self.refresh_tailscale_status)

        restart_btn = QtWidgets.QPushButton("Restart Setup")
        self._set_button_role(restart_btn, "secondary")
        restart_btn.clicked.connect(self._restart_setup)

        actions.addStretch(1)
        actions.addWidget(refresh_btn)
        actions.addWidget(restart_btn)
        actions.addStretch(1)
        card.addLayout(actions)

        self._tailscale_state_labels.append(self.complete_tailscale_state_label)
        return panel

    # ----- compact mode -----
    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_compact_layout()
        if hasattr(self, "route_image_display"):
            self._render_route_guide_image()

    def _apply_compact_layout(self) -> None:
        compact = self.height() < 740
        if compact == self._compact_mode:
            return
        self._compact_mode = compact

        self.header_note_label.setVisible(not compact)
        for i in range(self.header_link_row.count()):
            item = self.header_link_row.itemAt(i)
            widget = item.widget()
            if widget is not None:
                widget.setVisible(not compact)

        self.wizard_hint_label.setVisible(not compact)
        if compact and self.show_route_images_var.get():
            self.show_route_images_var.set(False)
            self._apply_route_guide_visibility()

    # ----- wizard helpers -----
    def _toggle_route_images(self) -> None:
        self.show_route_images_var.set(not self.show_route_images_var.get())
        self._apply_route_guide_visibility()

    def _apply_route_guide_visibility(self) -> None:
        show = self.show_route_images_var.get()
        self.route_images_frame.setVisible(show)
        self.route_guide_btn.setText("Hide Screenshots" if show else "Show Me How")

        # Keep the guide in view without scrolling by collapsing non-essential rows.
        self.route_admin_link_label.setVisible(not show)
        self.ts_routes_label.setVisible(not show)
        self.ts_route_approval_label.setVisible(not show)

        if show:
            self._render_route_guide_image()
            if hasattr(self, "route_scroll_area"):
                QtCore.QTimer.singleShot(
                    0,
                    lambda: self.route_scroll_area.ensureWidgetVisible(self.route_images_frame, 8, 12),
                )

    def _change_route_guide_image(self, delta: int) -> None:
        if not hasattr(self, "route_guide_steps") or not self.route_guide_steps:
            return
        count = len(self.route_guide_steps)
        self.route_guide_index = (self.route_guide_index + delta) % count
        self._render_route_guide_image()

    def _render_route_guide_image(self) -> None:
        if not hasattr(self, "route_guide_steps") or not self.route_guide_steps:
            return

        count = len(self.route_guide_steps)
        idx = max(0, min(self.route_guide_index, count - 1))
        self.route_guide_index = idx
        key, title = self.route_guide_steps[idx]

        self.route_image_title_label.setText(title)
        self.route_page_label.setText(f"Image {idx + 1} of {count}")
        self.route_prev_btn.setEnabled(count > 1)
        self.route_next_btn.setEnabled(count > 1)

        pm = self.route_pixmaps.get(key)
        if pm is None:
            self.route_image_display.setPixmap(QtGui.QPixmap())
            self.route_image_display.setText("Image unavailable")
            return

        width_hint = self.route_images_frame.width()
        if width_hint < 220:
            width_hint = self.width() - 120
        target_w = max(360, min(1040, width_hint - 24))
        target_h = max(160, min(420, int(self.height() * 0.32)))
        scaled = pm.scaled(
            target_w,
            target_h,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        self.route_image_display.setText("")
        self.route_image_display.setPixmap(scaled)

    def _set_connect_controls_visible(self, visible: bool) -> None:
        show = bool(visible)
        self.connect_controls_frame.setVisible(show)
        self.connect_controls_toggle_btn.setText("Hide Connection Controls" if show else "Expand Connection Controls")

    def _set_subnet_advanced_visible(self, visible: bool) -> None:
        show = bool(visible)
        self.subnet_advanced_frame.setVisible(show)
        self.subnet_expand_btn.setText("Hide Optional Subnet" if show else "Expand Optional Subnet")

    def _set_connect_feedback(self, message: str, tone: str = "info", timeout_ms: int = 3500) -> None:
        self.connect_feedback_var.set(message)
        color = {
            "ok": self.theme["feedback_ok"],
            "warn": self.theme["feedback_warn"],
            "error": self.theme["feedback_bad"],
            "info": self.theme["feedback_info"],
        }.get(tone, self.theme["feedback_info"])
        self.connect_feedback_label.setStyleSheet(f"color:{color}; font-size:12px; font-weight:600;")

        if self._connect_feedback_clear_timer is not None:
            self._connect_feedback_clear_timer.stop()
            self._connect_feedback_clear_timer.deleteLater()
            self._connect_feedback_clear_timer = None
        if timeout_ms > 0:
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._clear_connect_feedback)
            timer.start(timeout_ms)
            self._connect_feedback_clear_timer = timer

    def _clear_connect_feedback(self) -> None:
        self.connect_feedback_var.set("")
        self.connect_feedback_label.setStyleSheet(f"color:{self.theme['feedback_info']}; font-size:12px;")

    def _set_auth_key_feedback(self, message: str, tone: str = "info", timeout_ms: int = 3500) -> None:
        if not hasattr(self, "auth_key_feedback_label"):
            return
        color = {
            "ok": self.theme["feedback_ok"],
            "warn": self.theme["feedback_warn"],
            "error": self.theme["feedback_bad"],
            "info": self.theme["feedback_info"],
        }.get(tone, self.theme["feedback_info"])
        self.auth_key_feedback_label.setText(message)
        self.auth_key_feedback_label.setStyleSheet(f"color:{color}; font-size:12px; font-weight:600;")

        if self._auth_key_feedback_clear_timer is not None:
            self._auth_key_feedback_clear_timer.stop()
            self._auth_key_feedback_clear_timer.deleteLater()
            self._auth_key_feedback_clear_timer = None
        if timeout_ms > 0 and message:
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self._set_auth_key_feedback("", timeout_ms=0))
            timer.start(timeout_ms)
            self._auth_key_feedback_clear_timer = timer

    def _set_tailscale_state_style(self, state: str) -> None:
        color = {
            "good": self.theme["state_good"],
            "warn": self.theme["state_warn"],
            "bad": self.theme["state_bad"],
        }.get(state, self.theme["feedback_info"])
        for label in self._tailscale_state_labels:
            label.setStyleSheet(f"font-weight: 600; color: {color};")

    def _set_route_approval_banner(self, title: str, detail: str, tone: str) -> None:
        tone_map = {
            "ok": (self.theme["route_ok_bg"], self.theme["route_ok_fg"]),
            "warn": (self.theme["route_warn_bg"], self.theme["route_warn_fg"]),
            "error": (self.theme["route_bad_bg"], self.theme["route_bad_fg"]),
            "info": (self.theme["route_info_bg"], self.theme["route_info_fg"]),
        }
        bg, fg = tone_map.get(tone, tone_map["info"])
        self.route_status_banner.setText(title)
        self.route_status_banner.setStyleSheet(
            f"border-radius:12px; padding:10px 12px; font-weight:600; background:{bg}; color:{fg};"
        )
        self.route_approval_help_var.set(detail)

    def _update_header_tailscale_badge(self, state: str = "info") -> None:
        tone_map = {
            "good": (self.theme["chip_good_bg"], self.theme["chip_good_fg"]),
            "warn": (self.theme["chip_warn_bg"], self.theme["chip_warn_fg"]),
            "bad": (self.theme["chip_bad_bg"], self.theme["chip_bad_fg"]),
            "info": (self.theme["chip_info_bg"], self.theme["chip_info_fg"]),
        }
        bg, fg = tone_map.get(state, tone_map["info"])
        self.header_tailscale_chip.setStyleSheet(
            f"border-radius:12px; padding:6px 11px; font-size:12px; font-weight:600; background:{bg}; color:{fg};"
        )

    def _validate_auth_key_format(self) -> None:
        key = self.ts_auth_key_var.get().strip()
        if key.startswith("tskey-"):
            self._set_auth_key_feedback("Auth key format looks valid.", tone="ok")
        else:
            self._set_auth_key_feedback("Auth key must start with tskey-.", tone="warn", timeout_ms=5000)

    def _show_wizard_step_panel(self, step_index: int) -> None:
        self.step_stack.setCurrentIndex(step_index)

    # ----- persistence -----
    def _load_settings(self) -> None:
        if not self.settings_path.exists():
            return
        try:
            with self.settings_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.hostname_var.set(str(data.get("hostname") or DEFAULT_HOSTNAME))
            self.port_var.set(str(data.get("port") or DEFAULT_PORT))
            self.ts_subnet_var.set(str(data.get("subnet") or ""))
            self.ts_tailnet_var.set(str(data.get("tailnet") or ""))
            self.ts_auth_key_var.set(str(data.get("authKey") or ""))
        except Exception:
            pass

    def _save_settings(self) -> None:
        payload = {
            "hostname": self.hostname_var.get().strip() or DEFAULT_HOSTNAME,
            "port": self.port_var.get().strip() or DEFAULT_PORT,
            "subnet": self.ts_subnet_var.get().strip(),
            "tailnet": self.ts_tailnet_var.get().strip(),
            "authKey": self.ts_auth_key_var.get().strip(),
        }
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        with self.settings_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    # ----- misc -----
    def _detected_local_ip(self) -> str:
        if network_utils is None:
            return ""
        try:
            ip = str(network_utils.get_local_lan_ip() or "").strip()  # type: ignore[attr-defined]
            return ip
        except Exception:
            return ""

    def _update_dashboard_url(self) -> None:
        try:
            port = self._validated_port()
        except ValueError:
            port = DEFAULT_PORT
        ip = self.gateway_ip_var.get().strip() or ""
        if not ip or ip == "-":
            ip = "127.0.0.1"
        self.gateway_url_var.set(f"http://{ip}:{port}")

    def _refresh_local_network_identity(self, ip_hint: Optional[str] = None) -> None:
        ip = (ip_hint or "").strip()
        if not ip:
            ip = self._detected_local_ip()
        self.gateway_ip_var.set(ip or "-")
        self._update_dashboard_url()

    # ----- logs/process -----
    def _log(self, message: str) -> None:
        self.log_text.append(message.rstrip())

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
            if "Address already in use" in line and not self._port_conflict_notified:
                self._port_conflict_notified = True
                self.status_var.set("Gateway Port In Use")
                self._error("Port In Use", "Gateway failed to start because the selected port is already in use.")

    def _set_gateway_start_visual(self, running: bool) -> None:
        role = "success" if running else "primary"
        text = "Gateway Running" if running else "Start Gateway"

        self._set_button_role(self.start_btn, role)
        self.start_btn.setText(text)

        if hasattr(self, "step_start_btn"):
            self._set_button_role(self.step_start_btn, role)
            self.step_start_btn.setText(text)

    def _refresh_status(self) -> None:
        self._update_dashboard_url()
        running = self.proc is not None and self.proc.poll() is None
        if running:
            self.external_gateway_running = False
            self.status_var.set("Gateway Running")
            self.start_btn.setEnabled(False)
            if hasattr(self, "step_start_btn"):
                self.step_start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self._set_gateway_start_visual(True)
        else:
            external_running = False
            try:
                external_running = self._existing_gateway_status(self._validated_port()) is not None
            except ValueError:
                external_running = False
            if external_running:
                self.external_gateway_running = True
                self.status_var.set("Gateway Service Running (already active)")
                self.start_btn.setEnabled(False)
                if hasattr(self, "step_start_btn"):
                    self.step_start_btn.setEnabled(False)
                self.stop_btn.setEnabled(True)
                self._set_gateway_start_visual(True)
            else:
                self.external_gateway_running = False
                self.status_var.set("Gateway Stopped")
                self.start_btn.setEnabled(True)
                if hasattr(self, "step_start_btn"):
                    self.step_start_btn.setEnabled(True)
                self.stop_btn.setEnabled(False)
                self._set_gateway_start_visual(False)
            if self.proc is not None and self.proc.poll() is not None:
                self.proc = None

    # ----- api helpers -----
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

    @staticmethod
    def _api_get_port(path: str, port: str, timeout: float = 2.5) -> Dict[str, Any]:
        req = urllib.request.Request(url=f"http://127.0.0.1:{port}{path}", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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

    @staticmethod
    def _is_port_listening(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return True
        except OSError:
            return False

    def _existing_gateway_status(self, port: str) -> Optional[Dict[str, Any]]:
        try:
            data = self._api_get_port("/api/status", port, timeout=2.0)
        except Exception:
            return None
        agent_id = str(data.get("agentId") or "").strip().lower()
        if data.get("ok") and "hashwatcher-gateway" in agent_id:
            return data
        return None

    # ----- gateway controls -----
    def start_gateway(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        try:
            port = self._validated_port()
        except ValueError as exc:
            self._error("Invalid Port", str(exc))
            return

        hostname = self.hostname_var.get().strip() or DEFAULT_HOSTNAME
        self.hostname_var.set(hostname)
        self.port_var.set(port)
        self._save_settings()
        self._update_dashboard_url()
        self._port_conflict_notified = False
        self.external_gateway_running = False

        if self._is_port_listening(int(port)):
            existing = self._existing_gateway_status(port)
            if existing is not None:
                network = existing.get("network") or {}
                self._refresh_local_network_identity(ip_hint=str(network.get("localIp") or ""))
                self.external_gateway_running = True
                self.status_var.set("Gateway Service Running (already active)")
                self.start_btn.setEnabled(False)
                if hasattr(self, "step_start_btn"):
                    self.step_start_btn.setEnabled(False)
                self.stop_btn.setEnabled(True)
                self._set_gateway_start_visual(True)
                self._log(f"[gui] existing HashWatcher gateway already running on port {port}.")
                self._info("Gateway Already Running", f"A HashWatcher gateway is already running on port {port}. Reusing existing service.")
                return
            self.status_var.set("Gateway Port In Use")
            self._set_gateway_start_visual(False)
            self._log(f"[gui] cannot start: port {port} is already in use by another process.")
            self._error("Port In Use", f"Port {port} is already in use by another app. Stop that app or choose a different port.")
            return

        env = os.environ.copy()
        env["PI_HOSTNAME"] = hostname
        env["STATUS_HTTP_PORT"] = port
        env["STATUS_HTTP_BIND"] = DEFAULT_BIND

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
            self._error("Failed to Start", str(exc))
            self.proc = None
            self._set_gateway_start_visual(False)
            return

        threading.Thread(target=self._read_process_output, args=(self.proc,), daemon=True).start()
        self.start_btn.setEnabled(False)
        if hasattr(self, "step_start_btn"):
            self.step_start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._set_gateway_start_visual(True)
        self._log(f"[gui] started gateway on {DEFAULT_BIND}:{port} as {hostname}")
        QtCore.QTimer.singleShot(1200, self.refresh_tailscale_status)

    def stop_gateway(self) -> None:
        proc = self.proc
        if proc is not None and proc.poll() is None:
            self._log("[gui] stopping gateway...")
            proc.terminate()
            try:
                proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
            self._log("[gui] gateway stopped.")
            self.proc = None
            self._set_gateway_start_visual(False)
            return

        self.proc = None
        if not self.external_gateway_running:
            return

        try:
            port = int(self._validated_port())
        except ValueError:
            self._error("Stop Gateway", "Invalid port.")
            return

        if not self._confirm("Stop Existing Gateway", f"Stop the existing gateway process listening on port {port}?"):
            return

        ok, detail = self._stop_external_gateway_process(port)
        if ok:
            self._log(f"[gui] stopped external gateway on port {port}.")
            self.status_var.set("Gateway Stopped")
            self.external_gateway_running = False
            QtCore.QTimer.singleShot(300, self._refresh_status)
            return
        self._error("Stop Gateway", detail)

    def _stop_external_gateway_process(self, port: int) -> tuple[bool, str]:
        if psutil is None:
            return self._stop_external_gateway_process_without_psutil(port)

        try:
            conns = psutil.net_connections(kind="tcp")
        except Exception as exc:
            return False, f"Unable to inspect listening processes: {exc}"

        pids: list[int] = []
        for conn in conns:
            try:
                laddr = conn.laddr
                if not laddr:
                    continue
                lport = getattr(laddr, "port", None)
                if lport is None and isinstance(laddr, tuple) and len(laddr) >= 2:
                    lport = int(laddr[1])
                if lport != port:
                    continue
                status = str(getattr(conn, "status", "") or "")
                if status and "LISTEN" not in status.upper():
                    continue
                pid = getattr(conn, "pid", None)
                if pid:
                    pids.append(int(pid))
            except Exception:
                continue

        unique_pids = sorted(set(pids))
        if not unique_pids:
            return False, f"No listening gateway process found on port {port}."

        stopped = 0
        errors: list[str] = []
        for pid in unique_pids:
            try:
                proc = psutil.Process(pid)
                cmdline = " ".join(proc.cmdline()).lower()
                if "hashwatcher" not in cmdline and "hub_agent.py" not in cmdline and "main.py" not in cmdline:
                    errors.append(f"Skipped PID {pid}: not recognized as HashWatcher gateway.")
                    continue
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except psutil.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                stopped += 1
            except Exception as exc:
                errors.append(f"PID {pid}: {exc}")

        if stopped > 0:
            return True, ""
        if errors:
            return False, "; ".join(errors)
        return False, "Unable to stop the existing gateway process."

    def _stop_external_gateway_process_without_psutil(self, port: int) -> tuple[bool, str]:
        pids = self._list_listening_pids_without_psutil(port)
        if not pids:
            return False, f"No listening process found on port {port}."

        stopped = 0
        errors: list[str] = []
        for pid in sorted(set(pids)):
            ok, err = self._terminate_pid_without_psutil(pid)
            if ok:
                stopped += 1
            else:
                errors.append(err or f"PID {pid}: failed")

        if stopped > 0:
            return True, ""
        return False, "; ".join(errors) if errors else "Unable to stop the existing gateway process."

    def _list_listening_pids_without_psutil(self, port: int) -> list[int]:
        if os.name == "nt":
            return self._list_windows_listening_pids(port)
        return self._list_unix_listening_pids(port)

    def _list_unix_listening_pids(self, port: int) -> list[int]:
        pids: set[int] = set()
        lsof_bin = shutil.which("lsof")
        if lsof_bin:
            try:
                result = subprocess.run(
                    [lsof_bin, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    check=False,
                )
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line.isdigit():
                        pids.add(int(line))
            except Exception:
                pass
        if pids:
            return list(pids)

        ss_bin = shutil.which("ss")
        if ss_bin:
            try:
                result = subprocess.run(
                    [ss_bin, "-ltnp"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    check=False,
                )
                for line in result.stdout.splitlines():
                    if f":{port} " not in line:
                        continue
                    marker = "pid="
                    idx = line.find(marker)
                    if idx == -1:
                        continue
                    start = idx + len(marker)
                    end = start
                    while end < len(line) and line[end].isdigit():
                        end += 1
                    pid_str = line[start:end]
                    if pid_str.isdigit():
                        pids.add(int(pid_str))
            except Exception:
                pass
        return list(pids)

    def _list_windows_listening_pids(self, port: int) -> list[int]:
        pids: set[int] = set()
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                timeout=6,
                check=False,
            )
        except Exception:
            return []

        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if "LISTENING" not in line.upper():
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            local_addr = parts[1]
            pid_str = parts[-1]
            if f":{port}" not in local_addr:
                continue
            if pid_str.isdigit():
                pids.add(int(pid_str))
        return list(pids)

    def _terminate_pid_without_psutil(self, pid: int) -> tuple[bool, str]:
        if os.name == "nt":
            try:
                result = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=6,
                    check=False,
                )
                if result.returncode == 0:
                    return True, ""
                stderr = (result.stderr or result.stdout or "").strip()
                return False, f"PID {pid}: {stderr or 'taskkill failed'}"
            except Exception as exc:
                return False, f"PID {pid}: {exc}"

        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True, ""
        except Exception as exc:
            return False, f"PID {pid}: {exc}"

        deadline = time.time() + 4.0
        while time.time() < deadline:
            if not self._pid_exists(pid):
                return True, ""
            time.sleep(0.12)

        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True, ""
        except Exception as exc:
            return False, f"PID {pid}: {exc}"

        return True, ""

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    # ----- tailscale -----
    def generate_auth_key_via_api(self) -> None:
        tailnet = self.ts_tailnet_var.get().strip()
        api_key = self.ts_api_key_var.get().strip()
        if not tailnet:
            self._warn("Missing Tailnet", "Enter Tailnet first (example: your-tailnet or example.com).")
            return
        if not api_key:
            self._warn("Missing API Key", "Enter Tailscale API key first.")
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
            self._error("API Error", detail or str(exc))
            return
        except Exception as exc:
            self._error("API Error", str(exc))
            return

        key = str(data.get("key") or "").strip()
        if not key:
            self._error("API Error", "No auth key returned by Tailscale API.")
            return

        self.ts_auth_key_var.set(key)
        self._set_auth_key_feedback("Auth key generated and filled.", tone="ok")
        self._save_settings()
        self._log("[gui] generated auth key via Tailscale API.")
        self._info("Auth Key Generated", "Auth key generated and filled in automatically.")

    def refresh_tailscale_status(self) -> None:
        try:
            data = self._api_get("/api/status")
        except urllib.error.URLError:
            self._refresh_local_network_identity()
            self.last_ts_status = {
                "gatewayReachable": False,
                "authenticated": False,
                "online": False,
                "routesApproved": False,
                "routesPending": False,
            }
            self.ts_state_var.set("Tailscale: Waiting for gateway")
            self.header_tailscale_var.set("Tailscale: Waiting for gateway")
            self._set_tailscale_state_style("warn")
            self._update_header_tailscale_badge("warn")
            self.ts_ip_var.set("Tailscale IP: -")
            self.ts_routes_var.set("Advertised Routes: -")
            self.ts_route_approval_var.set("Route Approval: -")
            self._set_route_approval_banner(
                "Gateway API Unreachable",
                "Start the gateway first, then refresh. Route approval is checked live from the Tailscale API status.",
                "error",
            )
            return
        except Exception:
            self._refresh_local_network_identity()
            self.last_ts_status = {
                "gatewayReachable": False,
                "authenticated": False,
                "online": False,
                "routesApproved": False,
                "routesPending": False,
            }
            self.ts_state_var.set("Tailscale: Status check failed")
            self.header_tailscale_var.set("Tailscale: Status check failed")
            self._set_tailscale_state_style("bad")
            self._update_header_tailscale_badge("bad")
            self.ts_ip_var.set("Tailscale IP: -")
            self.ts_routes_var.set("Advertised Routes: -")
            self.ts_route_approval_var.set("Route Approval: -")
            self._set_route_approval_banner(
                "Status Read Error",
                "Could not read current Tailscale route approval state from the gateway API.",
                "error",
            )
            return

        tailscale = data.get("tailscale") or {}
        network = data.get("network") or {}
        self._refresh_local_network_identity(ip_hint=str(network.get("localIp") or ""))

        online = bool(tailscale.get("online"))
        authenticated = bool(tailscale.get("authenticated"))
        ip = tailscale.get("ip") or "-"
        routes = tailscale.get("advertisedRoutes") or []
        routes_approved = bool(tailscale.get("routesApproved"))
        routes_pending = bool(tailscale.get("routesPending"))
        key_expired = bool(tailscale.get("keyExpired"))

        self.last_ts_status = {
            "gatewayReachable": True,
            "authenticated": authenticated,
            "online": online,
            "routesApproved": routes_approved,
            "routesPending": routes_pending,
        }

        if key_expired:
            self.ts_state_var.set("Tailscale: Session expired - add a new auth key")
            self.header_tailscale_var.set("Tailscale: Session expired")
            self._set_tailscale_state_style("bad")
            self._update_header_tailscale_badge("bad")
        elif online and authenticated and routes_approved:
            self.ts_state_var.set("Tailscale: Connected and ready")
            self.header_tailscale_var.set("Tailscale: Connected and ready")
            self._set_tailscale_state_style("good")
            self._update_header_tailscale_badge("good")
        elif online and authenticated:
            self.ts_state_var.set("Tailscale: Connected - approve subnet route next")
            self.header_tailscale_var.set("Tailscale: Connected - route approval needed")
            self._set_tailscale_state_style("warn")
            self._update_header_tailscale_badge("warn")
        elif authenticated:
            self.ts_state_var.set("Tailscale: Signed in - waiting to come online")
            self.header_tailscale_var.set("Tailscale: Signed in - waiting")
            self._set_tailscale_state_style("warn")
            self._update_header_tailscale_badge("warn")
        else:
            self.ts_state_var.set("Tailscale: Not connected yet")
            self.header_tailscale_var.set("Tailscale: Not connected")
            self._set_tailscale_state_style("warn")
            self._update_header_tailscale_badge("warn")

        self.ts_ip_var.set(f"Tailscale IP: {ip}")

        routes_str = ", ".join(str(route) for route in routes) if routes else "-"
        self.ts_routes_var.set(f"Advertised Routes: {routes_str}")

        detected_subnet = network.get("detectedSubnet") or "-"
        machine_name = self.hostname_var.get().strip() or DEFAULT_HOSTNAME

        if routes_approved:
            approval = f"approved (subnet {detected_subnet})"
            self._set_route_approval_banner(
                "Subnet Approved",
                f"Tailscale API confirms route approval for {detected_subnet}. Machine {machine_name} is ready for remote LAN access.",
                "ok",
            )
        elif routes_pending:
            approval = f"pending approval in Tailscale admin (subnet {detected_subnet})"
            self._set_route_approval_banner(
                "Approval Still Pending",
                f"Tailscale API still shows route pending for {detected_subnet}. Use the approval link below, open {machine_name}, choose Edit route settings, and approve the subnet.",
                "warn",
            )
        elif authenticated and routes:
            approval = f"awaiting route confirmation (subnet {detected_subnet})"
            self._set_route_approval_banner(
                "Waiting for API Confirmation",
                f"The gateway is connected and advertising {routes_str}. If you just approved the route, wait a few seconds and press Refresh Status.",
                "info",
            )
        elif authenticated:
            approval = "connected, but subnet route is not advertised yet"
            self._set_route_approval_banner(
                "No Subnet Advertised Yet",
                "Connect the gateway with an auth key first so it can advertise the local subnet, then approve it in Machines using the link below.",
                "warn",
            )
        else:
            approval = "not yet configured"
            self._set_route_approval_banner(
                "Not Connected to Tailnet Yet",
                "Complete Step 3 (Connect Gateway). Route approval cannot happen until the machine is authenticated.",
                "info",
            )

        self.ts_route_approval_var.set(f"Route Approval: {approval}")

    def connect_tailscale(self) -> None:
        auth_key = self.ts_auth_key_var.get().strip()
        subnet = self.ts_subnet_var.get().strip()
        if not auth_key:
            self._set_connect_feedback("Add your auth key first.", tone="warn")
            self._warn("Missing Auth Key", "Generate or paste auth key first.")
            return

        payload: Dict[str, Any] = {"authKey": auth_key}
        if subnet:
            payload["subnetCIDR"] = subnet

        self._save_settings()
        self._set_connect_feedback("Connecting...", tone="info", timeout_ms=0)

        try:
            resp = self._api_post("/api/tailscale/setup", payload)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            self._set_connect_feedback("Connection failed. Check key and try again.", tone="error", timeout_ms=5500)
            self._error("Connect Failed", detail or str(exc))
            return
        except Exception as exc:
            self._set_connect_feedback("Connection failed. Retry in a few seconds.", tone="error", timeout_ms=5500)
            self._error("Connect Failed", str(exc))
            return

        if resp.get("ok"):
            self._log("[gui] tailscale connect requested.")
            self.refresh_tailscale_status()
            if bool(self.last_ts_status.get("authenticated")) and bool(self.last_ts_status.get("online")):
                if bool(self.last_ts_status.get("routesApproved")):
                    self._set_connect_feedback("Connected. Subnet route is approved.", tone="ok")
                else:
                    self._set_connect_feedback("Connected. Next: approve subnet route.", tone="warn")
            else:
                self._set_connect_feedback("Connection started. Checking status...", tone="info")
        else:
            self._set_connect_feedback("Connection failed. Check auth key and retry.", tone="error", timeout_ms=5500)
            self._error("Tailscale Error", str(resp.get("error") or "Unknown error"))

    def disconnect_tailscale(self) -> None:
        if not self._confirm("Disconnect", "Disconnect gateway from Tailscale?"):
            return
        self._post_simple_tailscale("/api/tailscale/logout", "Disconnect")

    def _post_simple_tailscale(self, path: str, action_name: str) -> None:
        def _disconnect_already_done(message: str) -> bool:
            text = message.lower()
            return (
                "no nodekey to log out" in text
                or "not logged in" in text
                or "already logged out" in text
                or "no state" in text
            )

        try:
            resp = self._api_post(path, {})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            try:
                parsed = json.loads(detail) if detail else {}
            except Exception:
                parsed = {}
            msg = str(parsed.get("error") or detail or str(exc))
            if action_name == "Disconnect" and _disconnect_already_done(msg):
                self._log("[gui] tailscale already disconnected.")
                self.refresh_tailscale_status()
                self._info("Disconnect", "Tailscale is already disconnected.")
                return
            self._error(action_name, msg)
            return
        except Exception as exc:
            self._error(action_name, str(exc))
            return

        if resp.get("ok"):
            self._log(f"[gui] tailscale action succeeded: {action_name.lower()}")
            self.refresh_tailscale_status()
            if action_name == "Disconnect":
                self.header_tailscale_var.set("Tailscale: Disconnecting...")
                self._update_header_tailscale_badge("warn")
                QtCore.QTimer.singleShot(1200, self.refresh_tailscale_status)
                QtCore.QTimer.singleShot(2800, self.refresh_tailscale_status)
        else:
            msg = str(resp.get("error") or "Unknown error")
            if action_name == "Disconnect" and _disconnect_already_done(msg):
                self._log("[gui] tailscale already disconnected.")
                self.refresh_tailscale_status()
                self._info("Disconnect", "Tailscale is already disconnected.")
                return
            self._error(action_name, msg)

    # ----- wizard -----
    def _wizard_back(self) -> None:
        if self.wizard_complete:
            return
        if self.wizard_step > 0:
            self.wizard_step -= 1
            self._render_wizard_step()

    def _wizard_next(self) -> None:
        if self.wizard_complete:
            return
        if not self._wizard_step_complete(self.wizard_step):
            return
        if self.wizard_step < len(self.wizard_steps) - 1:
            self.wizard_step += 1
            self._render_wizard_step()
        else:
            self.wizard_complete = True
            self.refresh_tailscale_status()
            self._render_wizard_step()

    def _render_wizard_step(self) -> None:
        if self.wizard_complete:
            self.wizard_group.setVisible(False)
            self.wizard_title_var.set("HashWatcher Setup Complete")
            self.wizard_body_var.set("Gateway is live and ready for remote access.")
            self.wizard_hint_var.set("Leave this app open. Restart Setup anytime below.")
            self.wizard_progress_var.set("●  ●  ●  ●  ●    Complete")
            self.wizard_action_btn.setVisible(False)
            self.wizard_back_btn.setVisible(False)
            self.wizard_next_btn.setVisible(False)
            self._update_wizard_step_status_visibility()
            self._show_wizard_step_panel(self.completion_page_index)
            return

        self.wizard_group.setVisible(True)
        step = self.wizard_steps[self.wizard_step]
        self.wizard_title_var.set(step["title"])
        self.wizard_body_var.set(step["body"])
        self.wizard_hint_var.set(step["hint"])
        self.wizard_progress_var.set(self._wizard_progress_text())
        self.wizard_action_btn.setText(step["action"])
        self.wizard_action_btn.setVisible(True)
        self.wizard_back_btn.setVisible(True)
        self.wizard_next_btn.setVisible(True)
        self.wizard_back_btn.setEnabled(self.wizard_step > 0)
        self.wizard_next_btn.setText("Finish" if self.wizard_step == len(self.wizard_steps) - 1 else "Next")
        self._update_wizard_step_status_visibility()
        self._show_wizard_step_panel(self.wizard_step)

    def _update_wizard_step_status_visibility(self) -> None:
        show = (self.wizard_step == 2) and (not self.wizard_complete)
        self.wizard_step_state_label.setVisible(show)
        self.wizard_step_ip_label.setVisible(show)

    def _restart_setup(self) -> None:
        self.wizard_complete = False
        self.wizard_group.setVisible(True)
        self.wizard_step = 0
        if hasattr(self, "show_route_images_var"):
            self.show_route_images_var.set(False)
            self._apply_route_guide_visibility()
        if hasattr(self, "connect_controls_toggle_btn"):
            self.connect_controls_toggle_btn.setChecked(False)
            self._set_connect_controls_visible(False)
        if hasattr(self, "subnet_expand_btn"):
            self.subnet_expand_btn.setChecked(False)
            self._set_subnet_advanced_visible(False)
        self._render_wizard_step()

    def _wizard_progress_text(self) -> str:
        filled = "●"
        empty = "○"
        parts = [filled if idx <= self.wizard_step else empty for idx in range(len(self.wizard_steps))]
        return "  ".join(parts) + f"    Step {self.wizard_step + 1} of {len(self.wizard_steps)}"

    def _wizard_step_complete(self, step_index: int) -> bool:
        self.refresh_tailscale_status()

        if step_index == 0:
            running = (self.proc is not None and self.proc.poll() is None) or bool(self.last_ts_status.get("gatewayReachable"))
            if not running:
                self._info("Step Incomplete", "Start the gateway first.")
                return False
        elif step_index == 1:
            key = self.ts_auth_key_var.get().strip()
            if not key.startswith("tskey-"):
                self._info("Step Incomplete", "Paste a valid auth key (tskey-...) first.")
                return False
        elif step_index == 2:
            if not bool(self.last_ts_status.get("authenticated")):
                self._info("Step Incomplete", "Connect Tailscale successfully first.")
                return False
        elif step_index == 3:
            if not bool(self.last_ts_status.get("routesApproved")):
                self._info("Step Incomplete", "Approve routes in Machines page, then refresh status.")
                return False
        elif step_index == 4:
            ready = bool(self.last_ts_status.get("authenticated")) and bool(self.last_ts_status.get("online")) and bool(self.last_ts_status.get("routesApproved"))
            if not ready:
                self._info("Not Ready", "Need online + route approved.")
                return False
        return True

    def _run_wizard_action(self) -> None:
        idx = self.wizard_step
        if idx == 0:
            self.start_gateway()
        elif idx == 1:
            if self.ts_auth_key_var.get().strip():
                self._validate_auth_key_format()
            else:
                self.open_tailscale_keys()
        elif idx == 2:
            self.connect_tailscale()
        elif idx == 3:
            self.open_tailscale_machines()
        elif idx == 4:
            self.refresh_tailscale_status()

    # ----- links -----
    @staticmethod
    def open_tailscale_keys() -> None:
        webbrowser.open("https://login.tailscale.com/admin/settings/keys")

    @staticmethod
    def open_tailscale_machines() -> None:
        webbrowser.open("https://login.tailscale.com/admin/machines")

    # ----- dialogs -----
    def _message_box(
        self,
        title: str,
        message: str,
        icon: QtWidgets.QMessageBox.Icon,
        buttons: QtWidgets.QMessageBox.StandardButton,
        default_button: Optional[QtWidgets.QMessageBox.StandardButton] = None,
    ) -> QtWidgets.QMessageBox:
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(message)
        box.setIcon(icon)
        box.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        box.setStandardButtons(buttons)
        if default_button is not None:
            box.setDefaultButton(default_button)
        box.setOption(QtWidgets.QMessageBox.Option.DontUseNativeDialog, True)
        box.setMinimumWidth(420)
        box.setMaximumWidth(560)
        box.setStyleSheet(
            """
            QMessageBox {
                background: #161A20;
                color: #F3F4F6;
            }
            QMessageBox QLabel#qt_msgbox_label {
                color: #F3F4F6;
                font-size: 14px;
                font-weight: 500;
            }
            QMessageBox QPushButton {
                border: 1px solid #344054;
                border-radius: 12px;
                background: #262A35;
                color: #F3F4F6;
                font-size: 13px;
                font-weight: 600;
                padding: 8px 16px;
                min-width: 96px;
                min-height: 30px;
            }
            QMessageBox QPushButton:hover { background: #313746; }
            QMessageBox QPushButton:pressed { background: #3A4151; }
            """
        )
        text_label = box.findChild(QtWidgets.QLabel, "qt_msgbox_label")
        if text_label is not None:
            text_label.setWordWrap(True)
            text_label.setMinimumWidth(320)
            text_label.setMaximumWidth(520)
        return box

    def _info(self, title: str, message: str) -> None:
        self._message_box(
            title=title,
            message=message,
            icon=QtWidgets.QMessageBox.Icon.Information,
            buttons=QtWidgets.QMessageBox.StandardButton.Ok,
            default_button=QtWidgets.QMessageBox.StandardButton.Ok,
        ).exec()

    def _warn(self, title: str, message: str) -> None:
        self._message_box(
            title=title,
            message=message,
            icon=QtWidgets.QMessageBox.Icon.Warning,
            buttons=QtWidgets.QMessageBox.StandardButton.Ok,
            default_button=QtWidgets.QMessageBox.StandardButton.Ok,
        ).exec()

    def _error(self, title: str, message: str) -> None:
        self._message_box(
            title=title,
            message=message,
            icon=QtWidgets.QMessageBox.Icon.Critical,
            buttons=QtWidgets.QMessageBox.StandardButton.Ok,
            default_button=QtWidgets.QMessageBox.StandardButton.Ok,
        ).exec()

    def _confirm(self, title: str, message: str) -> bool:
        result = self._message_box(
            title=title,
            message=message,
            icon=QtWidgets.QMessageBox.Icon.Question,
            buttons=QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            default_button=QtWidgets.QMessageBox.StandardButton.No,
        ).exec()
        return result == QtWidgets.QMessageBox.StandardButton.Yes

    # ----- lifecycle -----
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.proc is not None and self.proc.poll() is None:
            if not self._confirm(
                "Confirm Shutdown",
                "Are you sure you want to shut down the gateway? You will not have remote access",
            ):
                event.ignore()
                return
            self.stop_gateway()
        event.accept()



def _set_desktop_app_identity() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HashWatcher.GatewayDesktop")
    except Exception:
        pass


def main() -> None:
    _set_desktop_app_identity()
    app = QtWidgets.QApplication(sys.argv)
    QtCore.QCoreApplication.setApplicationName(APP_NAME)
    QtCore.QCoreApplication.setOrganizationName(APP_NAME)
    QtGui.QGuiApplication.setApplicationDisplayName(APP_NAME)
    window = GatewayGui()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
