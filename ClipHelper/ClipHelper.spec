# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    # uiautomation 运行时会从自带 bin/ 目录按名字加载 UIAutomationClient DLL，
    # 必须连同该目录一起打包，否则冻结后 UIA 枚举菜单静默失败（右键粘贴不判定）
    datas=collect_data_files('uiautomation', includes=['bin/*']),
    hiddenimports=['uiautomation', 'comtypes', 'psutil'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PIL', 'certifi', 'pip', 'setuptools'],
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
    name='ClipHelper',
    icon='ClipHelper.ico',
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
