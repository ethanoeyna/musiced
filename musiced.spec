# -*- mode: python ; coding: utf-8 -*-
#
# Cross-platform PyInstaller spec. Run on Windows → produces dist/Musiced.exe.
# Run on macOS → produces dist/Musiced.app (with lucyna:// URL scheme wired up
# via CFBundleURLTypes). Run on Linux → produces dist/Musiced binary.
#
# PyInstaller does NOT cross-compile: each platform must build its own artifact
# on a host of that platform. CI (.github/workflows/release.yml) runs the Mac
# build on a macos-latest runner when a v* tag is pushed.

import sys

_is_win = sys.platform.startswith('win')
_is_mac = sys.platform == 'darwin'

# ffmpeg/ffprobe live in ./bin/ — extension depends on the host OS.
_ffmpeg = 'bin/ffmpeg.exe' if _is_win else 'bin/ffmpeg'
_ffprobe = 'bin/ffprobe.exe' if _is_win else 'bin/ffprobe'

a = Analysis(
    ['musiced.py'],
    pathex=[],
    binaries=[],
    datas=[(_ffmpeg, 'bin'), (_ffprobe, 'bin')],
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
    a.binaries,
    a.datas,
    [],
    name='Musiced',
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

# macOS-only: wrap the binary in a proper .app bundle so the OS treats it as
# a real application (Dock icon, Launchpad entry, lucyna:// URL handler).
if _is_mac:
    app = BUNDLE(
        exe,
        name='Musiced.app',
        icon=None,
        bundle_identifier='dev.lucyna.musiced',
        info_plist={
            # Tell macOS this app handles lucyna:// URLs. Mirrors the Windows
            # HKCU registry entry musiced.py writes at runtime — but on macOS
            # this MUST be declared at packaging time in Info.plist; runtime
            # registration isn't a thing for URL schemes.
            'CFBundleURLTypes': [{
                'CFBundleURLName': 'dev.lucyna.musiced',
                'CFBundleURLSchemes': ['lucyna'],
            }],
            'CFBundleShortVersionString': '0.3.3',
            'CFBundleVersion': '0.3.3',
            'LSMinimumSystemVersion': '11.0',
            'NSHighResolutionCapable': True,
            # No need for microphone / files-and-folders entitlements — we
            # only read URLs and write to ~/Downloads.
        },
    )
