"""Run the model build in a separate process (plain Python, no ``uno``).

A build takes minutes, hundreds of MB and a lot of CPU: inside LibreOffice's
process it would slow down typing (Python's global interpreter lock) and use
LibreOffice's memory. So it runs ``python -m predictive.model_builder --json``
in a separate process, at low priority, and reads its JSON events.

Which Python? On Linux, LibreOffice uses the system Python; on Windows and
macOS it ships its own (``program/python.exe``, ``.../Resources/python``).
``sys.executable`` is not reliable inside LibreOffice (it can be ``soffice``),
so :func:`find_python` tries the likely locations and checks each candidate
by running it.
"""

import json
import logging
import os
import subprocess
import sys
import threading
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from predictive.logsetup import LOGGER_NAME

log = logging.getLogger(LOGGER_NAME + ".build")

# Folder that holds the "predictive" and "pressagio" packages (the
# extension's pythonpath): the child process imports them from there.
PYTHONPATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Windows: no console window, below-normal priority.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_BELOW_NORMAL_PRIORITY = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)

Event = Dict[str, object]


def python_candidates(program_dir: Optional[str] = None) -> List[str]:
    """Possible Python executables, most likely first (not checked)."""
    names = ["python3", "python"]
    if os.name == "nt":
        names = ["python.exe"]
    candidates: List[str] = []
    executable = sys.executable or ""
    if os.path.basename(executable).lower().startswith("python"):
        candidates.append(executable)
    for prefix in (sys.base_prefix, sys.prefix, program_dir):
        if not prefix:
            continue
        for sub in ("bin", "", os.pardir):
            for name in names:
                candidates.append(os.path.normpath(os.path.join(prefix, sub, name)))
    if program_dir:  # macOS: LibreOffice.app/Contents/{MacOS,Resources}
        candidates.append(os.path.join(program_dir, os.pardir, "Resources", "python"))
    seen = set()
    unique = []
    for path in candidates:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def works(python: str, timeout: float = 20.0) -> bool:
    """True if ``python`` runs and can import the extension's packages."""
    if not os.path.isfile(python):
        return False
    try:
        result = subprocess.run(
            [python, "-c", "import predictive.model_builder"],
            env=child_env(),
            capture_output=True,
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def find_python(
    program_dir: Optional[str] = None,
    candidates: Optional[Iterable[str]] = None,
    check: Callable[[str], bool] = works,
) -> Optional[str]:
    """First Python executable able to run the model builder, or None."""
    for path in (
        candidates if candidates is not None else python_candidates(program_dir)
    ):
        if check(path):
            log.info("model builder will run with %s", path)
            return path
    log.warning("no Python executable found to run the model builder")
    return None


def child_env() -> Dict[str, str]:
    """Environment of the child: only our packages on the path, UTF-8 output."""
    env = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONSTARTUP", "PYTHONNOUSERSITE"):
        env.pop(key, None)
    env["PYTHONPATH"] = PYTHONPATH
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def builder_command(
    python: str,
    out: str,
    language: str,
    sources: Sequence[str],
    max_download_mb: int,
    max_words: int,
    min_count: int,
) -> List[str]:
    """Command line of a build (sources: "wikisource", "folder:<path>"...)."""
    command = [
        python,
        "-m",
        "predictive.model_builder",
        "--json",
        "--out",
        out,
        "--lang",
        language,
        "--max-download-mb",
        str(int(max_download_mb)),
        "--max-words",
        str(int(max_words)),
        "--min-count",
        str(int(min_count)),
    ]
    for source in sources:
        command += ["--source", source]
    return command


class BuildProcess:
    """One build running in a separate process.

    ``on_event`` and ``on_exit`` are called from a reader thread: the caller
    must hand them over to its UI thread itself.
    """

    def __init__(
        self,
        command: Sequence[str],
        out: str,
        on_event: Callable[[Event], None],
        on_exit: Callable[[int, Optional[Event]], None],
    ) -> None:
        self.command = list(command)
        self.out = out
        self._on_event = on_event
        self._on_exit = on_exit
        self._process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self.cancelled = False
        self.last_event: Optional[Event] = None
        self.returncode: Optional[int] = None

    @property
    def running(self) -> bool:
        return self._process is not None and self.returncode is None

    def start(self) -> None:
        self._process = subprocess.Popen(
            self.command,
            env=child_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_CREATE_NO_WINDOW | _BELOW_NORMAL_PRIORITY,
        )
        self._thread = threading.Thread(target=self._read, name="pw-build", daemon=True)
        self._thread.start()
        log.info("model build started (pid %d)", self._process.pid)

    def cancel(self) -> None:
        """Ask the build to stop (SIGTERM on Linux/macOS, kill on Windows)."""
        if self._process is None or self.returncode is not None:
            return
        self.cancelled = True
        try:
            self._process.terminate()
        except OSError:
            pass

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        if self._thread is not None:
            self._thread.join(timeout)
        return self.returncode

    def _read(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        stderr_tail: List[str] = []

        def drain_stderr() -> None:
            assert process.stderr is not None
            for raw in process.stderr:
                stderr_tail.append(raw.decode("utf-8", "replace").rstrip())
                del stderr_tail[:-20]

        stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
        stderr_thread.start()
        for raw in process.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                event = {"event": "output", "text": line}
            if isinstance(event, dict):
                self.last_event = event
                try:
                    self._on_event(event)
                except Exception:
                    log.exception("build event handler failed")
        process.wait()
        stderr_thread.join(5)
        self.returncode = process.returncode
        if self.returncode != 0:
            # A killed child (Windows) cannot remove its partial file.
            for path in (self.out + ".part",):
                try:
                    os.remove(path)
                except OSError:
                    pass
            if self.cancelled and (self.last_event or {}).get("event") != "cancelled":
                self.last_event = {"event": "cancelled"}
            elif not self.cancelled and (self.last_event or {}).get("event") != "error":
                message = (
                    stderr_tail[-1] if stderr_tail else "exit code %d" % self.returncode
                )
                self.last_event = {"event": "error", "message": message}
        log.info(
            "model build finished: exit %s, %s",
            self.returncode,
            (self.last_event or {}).get("event"),
        )
        try:
            self._on_exit(self.returncode, self.last_event)
        except Exception:
            log.exception("build exit handler failed")
