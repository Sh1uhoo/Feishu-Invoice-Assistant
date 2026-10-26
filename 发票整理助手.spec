# -*- mode: python ; coding: utf-8 -*-
import os
import sys

_pydll = os.path.join(os.path.dirname(sys.executable), 'DLLs')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[(os.path.join(_pydll, 'libssl-3-x64.dll'), '.'), (os.path.join(_pydll, 'libcrypto-3-x64.dll'), '.')],
    datas=[],
    hiddenimports=[],
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
    name='发票整理助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
