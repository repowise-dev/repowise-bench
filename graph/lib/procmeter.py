"""Wall clock and peak memory for a child process, without psutil.

psutil is not in this environment and adding a dependency to measure a
dependency-free benchmark is the wrong trade. On Windows the number we want is
`PROCESS_MEMORY_COUNTERS.PeakWorkingSetSize`, which the kernel maintains for us
and which survives until the handle closes, so a single read after the child
exits is exact rather than sampled.

That last point matters. The obvious implementation polls RSS on a timer and
reports the largest sample, which systematically under-reports: a parse pool
that spikes for 200ms between two 500ms polls is invisible. The peak counter has
no such hole.

On non-Windows we fall back to `resource.getrusage(RUSAGE_CHILDREN)`, which is
also a true peak but is cumulative across all children reaped so far, so it is
only meaningful when one child is timed at a time. Stated rather than hidden,
because a benchmark that quietly changes what a column means across platforms is
worse than one that lacks the column.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_void_p),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_JobObjectExtendedLimitInformation = 9


class _Job:
    """A Windows job object, so peak memory covers the whole process tree.

    Querying the child handle alone is wrong for both arms here and it fails
    quietly rather than loudly. A venv `python.exe` re-execs, our parse pool
    spawns workers, and `codegraph` is node with its own workers, so the process
    Popen hands back can sit at 4 MB while its children allocate gigabytes. A
    job accumulates `PeakJobMemoryUsed` across every process in the tree.

    One known imprecision, stated rather than hidden: the child is assigned to
    the job immediately after `CreateProcess` rather than being created
    suspended, so memory a child allocates in the first few milliseconds of its
    life is not counted. For builds measured in seconds this is noise; for a
    sub-100ms command it is not, and such a command should not be using this.
    """

    def __init__(self) -> None:
        self.handle = ctypes.windll.kernel32.CreateJobObjectW(None, None)

    def assign(self, process_handle: int) -> bool:
        if not self.handle:
            return False
        return bool(
            ctypes.windll.kernel32.AssignProcessToJobObject(
                wintypes.HANDLE(self.handle), wintypes.HANDLE(process_handle)
            )
        )

    def peak_mb(self) -> float | None:
        if not self.handle:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        ok = ctypes.windll.kernel32.QueryInformationJobObject(
            wintypes.HANDLE(self.handle),
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        )
        if not ok:
            return None
        return info.PeakJobMemoryUsed / (1024 * 1024)

    def close(self) -> None:
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self.handle))
            self.handle = None


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _peak_rss_mb_windows(handle: int) -> float | None:
    counters = _PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(
        wintypes.HANDLE(handle), ctypes.byref(counters), counters.cb
    )
    if not ok:
        return None
    return counters.PeakWorkingSetSize / (1024 * 1024)


@dataclass(frozen=True)
class ProcResult:
    command: list[str]
    returncode: int
    seconds: float
    peak_rss_mb: float | None
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_measured(command: list[str], *, cwd: Path | str | None = None,
                 timeout: int = 3600, shell: bool = False,
                 env: dict[str, str] | None = None) -> ProcResult:
    """Run a command, returning wall clock and true peak working set.

    The peak is read while the handle is still open, immediately after the child
    exits. Reading it after `Popen.__exit__` closes the handle returns nothing,
    which is how this silently reports None if the calls are reordered.
    """
    job = _Job() if _IS_WINDOWS else None
    started = time.perf_counter()
    proc = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # Windows defaults these pipes to the ANSI codepage, and cp1252 cannot
        # decode the box-drawing glyphs `codegraph` prints. The reader thread
        # then dies with UnicodeDecodeError while communicate() returns normally,
        # so the run looks fine and its output is silently empty. errors=replace
        # keeps a mangled glyph rather than losing an error message.
        encoding="utf-8",
        errors="replace",
        shell=shell,
        # `None` inherits this process's environment, which is what every arm
        # but one wants. codebase-memory-mcp needs a per-build CBM_CACHE_DIR,
        # and passing it here rather than mutating os.environ keeps two arms
        # measured in the same session from inheriting each other's cache.
        env=env,
    )
    if job is not None:
        job.assign(int(proc._handle))  # noqa: SLF001 - the handle is the only way in
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        timed_out = True
    elapsed = time.perf_counter() - started

    peak = None
    if job is not None:
        # Read while the job handle is still open. The counter dies with it.
        peak = job.peak_mb()
        if not peak:
            # Job assignment can fail if the process is already in a job that
            # disallows nesting. Fall back to the single-process peak, which
            # under-reports a tree but beats reporting nothing.
            peak = _peak_rss_mb_windows(int(proc._handle))  # noqa: SLF001
        job.close()
    else:  # pragma: no cover - measurement host is Windows
        import resource

        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024

    if timed_out:
        return ProcResult(command, -9, elapsed, peak, stdout, f"TIMEOUT after {timeout}s\n{stderr}")
    return ProcResult(command, proc.returncode, elapsed, peak, stdout, stderr)


def dir_size_mb(path: Path) -> float:
    """Total size of a directory tree, for the index-on-disk column."""
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file()) / (1024 * 1024)
