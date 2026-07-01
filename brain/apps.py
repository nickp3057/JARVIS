"""Windows app launcher and manager for JARVIS.

Uses PowerShell Start-Process (searches PATH and Start Menu) so no extra
Python dependencies are needed.  Common aliases map natural-language names
to executable identifiers.
"""

from __future__ import annotations

import subprocess
from jarvis_logger import get_logger

log = get_logger(__name__)

# Common natural-language name → executable / process name pairs.
_OPEN_MAP: dict[str, str] = {
    "chrome":           "chrome",
    "google chrome":    "chrome",
    "browser":          "chrome",
    "spotify":          "spotify",
    "discord":          "discord",
    "cursor":           "cursor",
    "vs code":          "code",
    "vscode":           "code",
    "visual studio code": "code",
    "notepad":          "notepad",
    "notepad++":        "notepad++",
    "file explorer":    "explorer",
    "explorer":         "explorer",
    "files":            "explorer",
    "powershell":       "powershell",
    "terminal":         "wt",
    "windows terminal": "wt",
    "task manager":     "taskmgr",
    "calculator":       "calc",
    "paint":            "mspaint",
    "word":             "winword",
    "excel":            "excel",
    "outlook":          "outlook",
    "slack":            "slack",
    "teams":            "teams",
    "zoom":             "zoom",
    "obs":              "obs64",
    "steam":            "steam",
    "edge":             "msedge",
    "firefox":          "firefox",
    "vlc":              "vlc",
    "settings":         "ms-settings:",
}

# For close/focus we match against the running process name.
_PROCESS_MAP: dict[str, str] = {
    "chrome":           "chrome",
    "google chrome":    "chrome",
    "spotify":          "spotify",
    "discord":          "discord",
    "cursor":           "cursor",
    "vs code":          "code",
    "vscode":           "code",
    "notepad":          "notepad",
    "file explorer":    "explorer",
    "explorer":         "explorer",
    "powershell":       "powershell",
    "terminal":         "WindowsTerminal",
    "windows terminal": "WindowsTerminal",
    "slack":            "slack",
    "teams":            "teams",
    "zoom":             "zoom",
    "edge":             "msedge",
    "firefox":          "firefox",
}


def _run_ps(command: str, timeout: int = 10) -> tuple[bool, str]:
    """Run a PowerShell command. Returns (success, output)."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=timeout,
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if result.returncode != 0 and not out:
            return False, err or "Unknown error"
        return True, out
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def open_app(name: str) -> str:
    """Open an application by natural-language name."""
    key = name.strip().lower()
    exe = _OPEN_MAP.get(key, key)

    # ms-settings: URIs use Start directly
    if exe.startswith("ms-"):
        ok, out = _run_ps(f'Start-Process "{exe}"')
    else:
        ok, out = _run_ps(f'Start-Process "{exe}" -ErrorAction Stop')

    if ok:
        log.info("Opened app: %s (%s)", name, exe)
        return f"Opening {name.title()}."
    log.warning("Failed to open %s: %s", name, out)
    return f"Couldn't open {name} — it may not be installed or I don't recognise the name."


def close_app(name: str) -> str:
    """Close all processes matching an app name."""
    key = name.strip().lower()
    proc = _PROCESS_MAP.get(key, key)
    ok, out = _run_ps(f'Stop-Process -Name "{proc}" -Force -ErrorAction Stop')
    if ok:
        return f"Closed {name.title()}."
    return f"Couldn't close {name}: {out}"


def focus_app(name: str) -> str:
    """Bring an app window to the foreground."""
    key = name.strip().lower()
    proc = _PROCESS_MAP.get(key, key)
    ps = (
        'Add-Type -AssemblyName Microsoft.VisualBasic;'
        f'$p = Get-Process "{proc}" -ErrorAction SilentlyContinue | Select-Object -First 1;'
        'if ($p) { [Microsoft.VisualBasic.Interaction]::AppActivate($p.Id); "ok" } else { "not found" }'
    )
    ok, out = _run_ps(ps)
    if ok and out == "ok":
        return f"Brought {name.title()} to the front."
    return f"Couldn't find {name} — is it open?"


def list_open_apps() -> str:
    """List currently running apps that have a visible window."""
    ps = (
        'Get-Process | Where-Object {$_.MainWindowTitle -ne ""}'
        ' | Select-Object -ExpandProperty Name | Sort-Object -Unique'
        ' | Out-String -Width 200'
    )
    ok, out = _run_ps(ps)
    if not ok or not out.strip():
        return "I couldn't read the running apps."
    apps = [line.strip() for line in out.strip().splitlines() if line.strip()]
    return "Running apps: " + ", ".join(apps) + "."
