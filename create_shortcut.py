"""
One-time setup: creates a JARVIS desktop shortcut that can be pinned to
the Windows taskbar.

Run once from the project root:
    python create_shortcut.py

Uses pythonw.exe (no console window) so JARVIS launches cleanly as a
tray-style app.  The shortcut lands on your Desktop; right-click it and
choose 'Pin to taskbar'.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SHORTCUT_NAME = "JARVIS.lnk"


def find_pythonw() -> str:
    """Return the pythonw.exe path next to the current python.exe."""
    py = Path(sys.executable)
    pythonw = py.parent / "pythonw.exe"
    if pythonw.exists():
        return str(pythonw)
    # Fallback: use python.exe (shows a brief console flash on launch)
    return str(py)


def create_shortcut() -> None:
    pythonw = find_pythonw()
    project = str(PROJECT_ROOT)

    # PowerShell one-liner: create a WScript.Shell shortcut object and save it.
    ps_script = f"""
$ws  = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut("$env:USERPROFILE\\Desktop\\{SHORTCUT_NAME}")
$lnk.TargetPath       = '{pythonw}'
$lnk.Arguments        = '-m app.main'
$lnk.WorkingDirectory = '{project}'
$lnk.Description      = 'Pearson Stark JARVIS'
$lnk.Save()
Write-Host "Shortcut created on Desktop."
""".strip()

    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        print(f"✓  JARVIS shortcut created on your Desktop.")
        print(f"   Right-click it → 'Pin to taskbar' to keep it in the bar.")
        print(f"   Target: {pythonw} -m app.main")
    else:
        print("✗  PowerShell shortcut creation failed:")
        print(result.stderr or result.stdout)
        print()
        print("Manual fallback:")
        print(f"  1. Right-click Desktop → New → Shortcut")
        print(f"  2. Target:  {pythonw} -m app.main")
        print(f"  3. Start in: {project}")
        print(f"  4. Name it JARVIS, then right-click → Pin to taskbar")


if __name__ == "__main__":
    create_shortcut()
