# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_dynamic_libs
cuda_binaries = collect_dynamic_libs('nvidia')

a = Analysis(
    ['main.py', './resource/config.py', './resource/model_utils.py'],
    pathex=['.\\.venv\\Lib\\site-packages'],
    binaries=cuda_binaries,
    datas=[('resource','resource')],
    hiddenimports=['PyQt6', 'winrt.windows.ui.viewmanagement', 'qfluentwidgets', 'pyaudio', 'numpy', 'faster_whisper', 'nvidia.cuda_runtime-cu12', 'nvidia.cublas-cu12', 'nvidia.cudnn-cu12'],
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
    manifest=None,
    icon='./resource/assets/icon.ico',
    disable_windowed_traceback=True,
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
