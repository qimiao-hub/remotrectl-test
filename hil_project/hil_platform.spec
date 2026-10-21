# -*- mode: python ; coding: utf-8 -*-

import sys
sys._pyi_isolated_subprocess = True

hiddenimports = [
    "h11",
    "websockets",
    "websockets.legacy",
    "websockets.legacy.server",
    "uvicorn.lifespan.on",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.websockets_impl",
    "yaml",
    "serial",
    "can",
    "can.interfaces",
    "can.interfaces.vector",
    "can.interfaces.pcan",
    "can.interfaces.socketcan",
    "can.interfaces.virtual",
    "cantools",
    "cantools.database",
]

excludes = [
    "pytest",
    "tests",
    "pygame",
    "pkg_resources",
    "setuptools",
]


a = Analysis(
    ["run_server.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("app/static", "app/static"),
        ("config.example.yaml", "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HILInterfacePlatform",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="HILInterfacePlatform",
)
