"""Test-only terminal setup and traceback support, after interpreter startup."""

from __future__ import annotations

import faulthandler
import fcntl
import os
import runpy
import signal
import sys
import termios


def main():
    slave = int(sys.argv[1])
    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
    os.close(slave)
    faulthandler.enable()
    faulthandler.register(signal.SIGUSR1, all_threads=True)
    arguments = sys.argv[2:]
    if arguments[0] == "-I":
        arguments = arguments[1:]
    if arguments[0] == "-m":
        module = arguments[1]
        sys.argv = arguments[1:]
        runpy.run_module(module, run_name="__main__", alter_sys=True)
    else:
        sys.argv = arguments
        runpy.run_path(arguments[0], run_name="__main__")


if __name__ == "__main__":
    main()
