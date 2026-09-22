"""Run the bootstrap HIR interpreter with a bounded, isolated lifetime."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import multiprocessing
import sys
import time
import traceback

from jack.interpreter import Interpreter
from jack.runtime_externs import default_runtime_externs


def _run_child(connection, program, arguments, externs):
    stdout, stderr = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = Interpreter(
                externs=default_runtime_externs() if externs is None else externs
            ).eval_hir_program(program, arguments)
        connection.send(("ok", status, stdout.getvalue(), stderr.getvalue()))
    except BaseException:
        connection.send(("error", traceback.format_exc()))
    finally:
        connection.close()


def run_hir_isolated(program, arguments, *, timeout=180, label=None, externs=None):
    context = multiprocessing.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_run_child, args=(sender, program, arguments, externs),
    )
    started = time.perf_counter()
    process.start()
    sender.close()
    try:
        if not receiver.poll(timeout):
            process.kill()
            process.join()
            raise AssertionError(
                f"interpreter timed out after {timeout}s: {label or arguments}"
            )
        try:
            result = receiver.recv()
        except EOFError as error:
            raise AssertionError(
                f"interpreter exited without a result: {label or arguments}"
            ) from error
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()
            raise AssertionError(
                f"interpreter did not exit after reporting: {label or arguments}"
            )
        if result[0] != "ok":
            raise AssertionError(f"interpreter failed: {label or arguments}\n{result[1]}")
        if label is not None:
            print(
                f"interpreter {label}: {time.perf_counter() - started:.2f}s",
                file=sys.stderr, flush=True,
            )
        return result[1:]
    finally:
        receiver.close()
