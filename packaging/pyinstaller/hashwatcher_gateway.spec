# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path(__file__).resolve().parents[2]

datas = [
    (str(project_root / "app" / "gateway" / "assets"), "app/gateway/assets"),
    (str(project_root / "app" / "vendor" / "tailscale"), "app/vendor/tailscale"),
]

hiddenimports = [
    "gateway.hub_agent",
    "gateway.tailscale_setup",
    "gateway.network_utils",
    "gateway.embedded_tailscale",
]

a = Analysis(
    [str(project_root / "app" / "gui.py")],
    pathex=[str(project_root / "app")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="HashWatcherGatewayDesktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

app = BUNDLE(
    exe,
    name="HashWatcherGatewayDesktop.app",
    bundle_identifier="com.hashwatcher.gateway.desktop",
)
