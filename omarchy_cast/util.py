import shutil
import subprocess


class CommandError(RuntimeError):
    def __init__(self, cmd, returncode, stderr):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"{' '.join(cmd)} failed ({returncode}): {stderr.strip()}")


def have(cmd):
    return shutil.which(cmd) is not None


def run(cmd, timeout=20, check=True):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise CommandError(cmd, 127, f"{cmd[0]}: not found")
    except subprocess.TimeoutExpired:
        raise CommandError(cmd, 124, f"{cmd[0]}: timed out")
    if check and proc.returncode != 0:
        raise CommandError(cmd, proc.returncode, proc.stderr)
    return proc


def pactl(args, timeout=20, check=True):
    return run(["pactl", *args], timeout=timeout, check=check)
