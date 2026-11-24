import os
import subprocess
import tempfile
from pathlib import Path
from threading import Lock


diagnostic_lock = Lock()

DIAGNOSTIC_TIMEOUT_SECONDS = 120
MAX_OUTPUT_BYTES = 24_000


def run_artemis_diagnostics() -> str:
    if os.name != "nt":
        return "Artemis diagnostics requires Windows."

    repository = Path(__file__).resolve().parents[2]
    script = repository / "scripts" / "Test-Artemis.ps1"

    windows_directory = Path(
        os.environ.get("SystemRoot", r"C:\Windows")
    )
    powershell = (
        windows_directory
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )

    if not script.is_file():
        return "Diagnostics could not start: Test-Artemis.ps1 was not found."

    if not powershell.is_file():
        return "Diagnostics could not start: Windows PowerShell was not found."

    if not diagnostic_lock.acquire(blocking=False):
        return "Artemis diagnostics is already running."

    try:
        # Capture output in a temporary file instead of allowing
        # unlimited output to accumulate in memory.
        with tempfile.TemporaryFile(mode="w+b") as output:
            try:
                process = subprocess.run(
                    [
                        str(powershell),
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script),
                        "-BaseUrl",
                        "http://127.0.0.1:8200",
                    ],
                    cwd=str(repository),
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    timeout=DIAGNOSTIC_TIMEOUT_SECONDS,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    check=False,
                )
                result = (
                    f"Diagnostics finished. Exit code: {process.returncode}.\n"
                    + (
                        "The script reported a passing exit code; "
                        "check its summary for warnings."
                        if process.returncode == 0
                        else "The script reported a failure. Review its output below."
                    )
                )
            except subprocess.TimeoutExpired:
                result = (
                    "Diagnostics exceeded the 120-second limit and "
                    "its PowerShell process was stopped. "
                    "The test did not finish."
                )

            output.seek(0)
            raw = output.read(MAX_OUTPUT_BYTES + 1)
            truncated = len(raw) > MAX_OUTPUT_BYTES
            raw = raw[:MAX_OUTPUT_BYTES]

            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("mbcs", errors="replace")

            if not text.strip():
                text = "No diagnostic output was captured."

            if truncated:
                text += "\n\n[Output truncated at the display limit.]"

            return (
                result
                + "\n\nOutput from Test-Artemis.ps1:\n\n"
                + text.strip()
            )

    except OSError as error:
        return (
            "Diagnostics could not run: "
            f"{error.strerror or type(error).__name__}"
        )
    finally:
        diagnostic_lock.release()