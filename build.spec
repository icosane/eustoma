# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py', './resource/config.py', './resource/model_utils.py' ],
    pathex=['.\\.venv\\Lib\\site-packages'],
    binaries=[],
    datas=[('assets','assets'),('resource','resource'),('lib','lib')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Eustoma',
    version="version.txt",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    manifest='dpi_aware.manifest',
    icon=None,
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
    name='main',
)
