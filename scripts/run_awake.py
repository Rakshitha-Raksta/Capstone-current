"""
Run a command while asking Windows not to sleep (SetThreadExecutionState), so a long experiment
is not suspended mid-run. It does not change the command or its methodology, and on non-Windows
systems it simply runs the command.

    python scripts/run_awake.py -- python scripts/run_experiments.py --name main --seeds 1 2 3
"""
import ctypes
import os
import subprocess
import sys

ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def main():
    args = sys.argv[1:]
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        sys.exit("usage: run_awake.py -- <command> [args...]")
    if os.path.isfile(args[0]):   # CreateProcess does not resolve relative interpreter paths
        args[0] = os.path.abspath(args[0])
    awake = sys.platform == "win32" and ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED) != 0
    print(f"keep-awake: {'on' if awake else 'unavailable'}", flush=True)
    try:
        return subprocess.call(args)
    finally:
        if awake:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


if __name__ == "__main__":
    sys.exit(main())
