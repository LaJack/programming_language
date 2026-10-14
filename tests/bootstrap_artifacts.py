"""Process-local builds for immutable bootstrap test inputs."""

from __future__ import annotations

import atexit
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import threading
import time

from jack.compiler_driver import CompilationOptions, CompilerDriver


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "selfhost/bootstrap/main.jack"
ROOTS = (ROOT / "selfhost", ROOT / "jack")


class BootstrapArtifacts:
    def __init__(self, driver_factory=CompilerDriver):
        self._driver_factory = driver_factory
        self._directory = None
        self._executables = {}
        self._programs = {}
        self._externs = {}
        self._lock = threading.RLock()

    def _key(self, entry, options, externs):
        return (
            Path(entry).resolve(),
            tuple(Path(root).resolve() for root in options.module_roots),
            tuple(sorted(options.import_overrides.items())),
            options.backend, options.optimization, options.debug, options.clang,
            None if externs is None else tuple(
                (name, id(binding)) for name, binding in sorted(externs.items())
            ),
        )

    def executable(self, entry=ENTRY, options=None, *, externs=None):
        options = options or CompilationOptions(module_roots=ROOTS)
        if options.save_temps is not None:
            raise ValueError("Saved-artifact tests must own their build directory.")
        key = self._key(entry, options, externs)
        with self._lock:
            if externs is not None:
                self._externs[key] = dict(externs)
            if key not in self._executables:
                if self._directory is None:
                    self._directory = tempfile.TemporaryDirectory(prefix="jack-bootstrap-tests-")
                output = Path(self._directory.name) / f"bootstrap-{len(self._executables)}"
                started = time.perf_counter()
                result = self._driver_factory(
                    print_handler=None, comptime_externs=externs,
                ).compile_executable(Path(entry).resolve(), replace(options, output=output))
                self._executables[key] = result
                print(f"bootstrap {options.backend} -O{options.optimization} build: "
                      f"{time.perf_counter() - started:.2f}s", file=sys.stderr, flush=True)
            return self._executables[key]

    def hir(self, entry=ENTRY, options=None, *, externs=None):
        options = options or CompilationOptions(module_roots=ROOTS)
        # Backend/link settings do not affect canonical interpreter HIR.
        options = replace(options, backend="llvm", optimization=0, debug=False,
                          clang="clang", output=None, save_temps=None)
        key = self._key(entry, options, externs)
        with self._lock:
            if externs is not None:
                self._externs[key] = dict(externs)
            if key not in self._programs:
                started = time.perf_counter()
                self._programs[key] = self._driver_factory(
                    print_handler=None, comptime_externs=externs,
                ).compile_hir(Path(entry).resolve(), options)
                print(f"bootstrap HIR build: {time.perf_counter() - started:.2f}s",
                      file=sys.stderr, flush=True)
            return deepcopy(self._programs[key])

    def close(self):
        with self._lock:
            self._executables.clear()
            self._programs.clear()
            self._externs.clear()
            if self._directory is not None:
                self._directory.cleanup()
                self._directory = None


artifacts = BootstrapArtifacts()
atexit.register(artifacts.close)
