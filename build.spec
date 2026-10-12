# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['test_full.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
    'pymem', 'pymem.process',
    'win32api', 'win32con', 'win32gui',
    'pystray', 'PIL', 'PIL.Image', 'PIL.ImageDraw',
    'tkinter', 'tkinter.ttk',
    'offsets_loader',   # ← добавить
],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='FearlessHack',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,                # console=True чтобы видеть логи. Поменяй на False для релиза
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                   # путь к .ico файлу (опционально)
)