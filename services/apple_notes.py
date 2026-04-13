import platform
import subprocess


def is_available() -> bool:
    return platform.system() == "Darwin"


def get_titles() -> list[str]:
    """Return all note titles from Apple Notes using newline delimiter to handle commas in titles."""
    if not is_available():
        return []
    # Use record separator (ASCII 30) to safely split titles that may contain commas
    script = (
        'tell application "Notes"\n'
        '  set output to ""\n'
        '  repeat with n in every note\n'
        '    set output to output & (name of n) & "\n"\n'
        '  end repeat\n'
        '  return output\n'
        'end tell'
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return [t.strip() for t in result.stdout.splitlines() if t.strip()]


def get_content(title: str) -> str:
    """Return the plaintext content of a note by title."""
    if not is_available():
        return ""
    # Escape backslashes first, then double-quotes
    safe = title.replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "Notes" to get plaintext of note "{safe}"'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.stdout.strip()
