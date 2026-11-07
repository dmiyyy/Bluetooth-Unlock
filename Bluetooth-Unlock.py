#!/usr/bin/env python3
"""Bluetooth-Unlock: lock the session when a Bluetooth device goes away
and (optionally) unlock it again when the device comes back.

Presence is detected with ``l2ping`` (needs root or cap_net_raw) and/or
``bluetoothctl info`` (works when the device is connected/paired).
"""
import argparse
import configparser
import getpass
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime

if sys.version_info < (3, 6):
    sys.exit("Python 3.6+ is required, please run with python3!")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(SCRIPT_DIR, "config.ini")
CONFIG_VERSION = "10.0"

MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")

ENVIRONMENTS = {
    # name: (lock command, unlock command)
    "LOGINCTL": (["loginctl", "lock-session"], ["loginctl", "unlock-session"]),
    "KDE": (["loginctl", "lock-session"], ["loginctl", "unlock-session"]),
    "GNOME": (["gnome-screensaver-command", "-l"], ["gnome-screensaver-command", "-d"]),
    "XSCREENSAVER": (["xscreensaver-command", "-lock"], ["xscreensaver-command", "-deactivate"]),
    "MATE": (["mate-screensaver-command", "-l"], ["mate-screensaver-command", "-d"]),
    "CINNAMON": (["cinnamon-screensaver-command", "-l"], ["cinnamon-screensaver-command", "-d"]),
}

DETECTION_METHODS = ("l2ping", "bluetoothctl", "both")

DEFAULTS = {
    "check_interval": "3",     # seconds between checks
    "fail_threshold": "3",     # consecutive misses before locking
    "ping_timeout": "2",       # seconds l2ping waits for an answer
    "unlock_on_return": "yes", # unlock automatically when the device returns
    "detection": "l2ping",     # l2ping | bluetoothctl | both
}

DEBUG = False


def log(*args):
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *args, flush=True)


def debug(*args):
    if DEBUG:
        log("[debug]", *args)


def run(cmd, timeout=10):
    """Run a command, return (returncode, combined output). Never raises."""
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout, universal_newlines=True)
        return proc.returncode, proc.stdout or ""
    except FileNotFoundError:
        return 127, "command not found: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except OSError as exc:
        return 126, str(exc)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
class Settings:
    def __init__(self, path):
        self.path = path
        self.cfg = configparser.ConfigParser()
        try:
            self.cfg.read(path)
        except configparser.Error as exc:
            log("Config file is broken (%s), starting with an empty one" % exc)
            self.cfg = configparser.ConfigParser()

    def get(self, section, option, fallback=None):
        return self.cfg.get(section, option, fallback=fallback)

    def set(self, section, option, value):
        if not self.cfg.has_section(section):
            self.cfg.add_section(section)
        self.cfg.set(section, option, str(value))

    def save(self):
        self.set("VERSION", "version", CONFIG_VERSION)
        for key, value in DEFAULTS.items():
            if not self.cfg.has_option("SETTINGS", key):
                self.set("SETTINGS", key, value)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            self.cfg.write(fh)
        os.replace(tmp, self.path)

    @property
    def device(self):
        value = self.get("DEVADDR", "devaddr")
        return value.strip().upper() if value else None

    @property
    def env(self):
        value = self.get("DESKTOP", "env")
        return value.strip().upper() if value else None

    def setting(self, key):
        return self.get("SETTINGS", key, DEFAULTS[key])

    def number(self, key, minimum):
        try:
            value = float(self.setting(key))
        except ValueError:
            log("Invalid value for %s, using default %s" % (key, DEFAULTS[key]))
            value = float(DEFAULTS[key])
        return max(value, minimum)

    def flag(self, key):
        return self.setting(key).strip().lower() in ("1", "yes", "true", "on", "y")


# --------------------------------------------------------------------------
# Interactive setup
# --------------------------------------------------------------------------
def ask(prompt, valid=None):
    while True:
        try:
            answer = input(prompt).strip()
        except EOFError:
            sys.exit("\nNo input available, run setup in a terminal (--setup).")
        if valid is None or answer.upper() in valid:
            return answer
        print("Please enter a valid option: %s" % ", ".join(valid))


def discover_devices():
    """Return a list of (address, name) of nearby / known devices."""
    found = {}
    try:  # PyBluez is optional
        import bluetooth  # noqa
        for addr, name in bluetooth.discover_devices(duration=8, lookup_names=True):
            found[addr.upper()] = name
    except Exception as exc:  # ImportError or adapter error
        debug("PyBluez discovery unavailable:", exc)

    if shutil.which("bluetoothctl"):
        print("Scanning with bluetoothctl for 10 seconds...")
        run(["bluetoothctl", "--timeout", "10", "scan", "on"], timeout=20)
        _, out = run(["bluetoothctl", "devices"])
        for line in out.splitlines():
            parts = line.strip().split(" ", 2)
            if len(parts) >= 2 and parts[0] == "Device" and MAC_RE.match(parts[1].upper()):
                found.setdefault(parts[1].upper(), parts[2] if len(parts) > 2 else "")
    return sorted(found.items())


def setup_device(settings):
    print("Searching for nearby devices (make sure your device is discoverable)...\n")
    devices = discover_devices()
    for idx, (addr, name) in enumerate(devices, 1):
        print("  %2d) %s  %s" % (idx, addr, name))
    if not devices:
        print("No devices found, you can still enter the address manually.")
    while True:
        answer = ask("\nEnter number from the list or Bluetooth address (AA:BB:CC:DD:EE:FF): ")
        if answer.isdigit() and 1 <= int(answer) <= len(devices):
            addr = devices[int(answer) - 1][0]
        else:
            addr = answer.upper()
        if MAC_RE.match(addr):
            break
        print("Invalid address format")
    settings.set("DEVADDR", "devaddr", addr)
    print("Device %s selected\n" % addr)


def setup_env(settings):
    print("Available desktop environments / lock methods:")
    names = list(ENVIRONMENTS)
    for name in names:
        tool = ENVIRONMENTS[name][0][0]
        status = "found" if shutil.which(tool) else "not found"
        note = " (recommended)" if name == "LOGINCTL" else ""
        print("  %-13s [%s: %s]%s" % (name, tool, status, note))
    env = ask("Enter your choice [LOGINCTL]: ", valid=names + [""]).upper() or "LOGINCTL"
    settings.set("DESKTOP", "env", env)
    print("%s selected\n" % env)


# --------------------------------------------------------------------------
# Session lock control
# --------------------------------------------------------------------------
class Session:
    def __init__(self, env):
        self.env = env
        self.session_id = self._find_session() if env in ("LOGINCTL", "KDE") else None
        debug("Session id:", self.session_id)

    @staticmethod
    def _find_session():
        sid = os.environ.get("XDG_SESSION_ID")
        if sid:
            return sid
        # Running outside the graphical session (e.g. systemd service):
        # use the user's display session.
        rc, out = run(["loginctl", "show-user", getpass.getuser(), "-p", "Display", "--value"])
        out = out.strip()
        return out if rc == 0 and out else None

    def _refresh(self):
        # The graphical session may appear after we start (autostart)
        if self.session_id is None and self.env in ("LOGINCTL", "KDE"):
            self.session_id = self._find_session()
            if self.session_id:
                debug("Session id:", self.session_id)

    def _cmd(self, base):
        self._refresh()
        cmd = list(base)
        if self.session_id and cmd[0] == "loginctl":
            cmd.append(self.session_id)
        return cmd

    def is_locked(self):
        """True/False if the real lock state is known, otherwise None."""
        self._refresh()
        if self.session_id:
            rc, out = run(["loginctl", "show-session", self.session_id,
                           "-p", "LockedHint", "--value"])
            out = out.strip().lower()
            if rc == 0 and out in ("yes", "no"):
                return out == "yes"
        return None

    def lock(self):
        rc, out = run(self._cmd(ENVIRONMENTS[self.env][0]))
        if rc != 0:
            log("Lock command failed (%d): %s" % (rc, out.strip()))
        return rc == 0

    def unlock(self):
        rc, out = run(self._cmd(ENVIRONMENTS[self.env][1]))
        if rc != 0:
            log("Unlock command failed (%d): %s" % (rc, out.strip()))
        return rc == 0


# --------------------------------------------------------------------------
# Device presence
# --------------------------------------------------------------------------
class DeviceError(Exception):
    """Detection itself is broken (permissions, no adapter...)."""


PERMISSION_HINTS = ("operation not permitted", "permission denied")
# "Host is down" / "No route to host" mean the device is simply absent,
# these mean the local adapter is missing or switched off:
ADAPTER_HINTS = ("no such device", "network is down", "can't get device info")


def ping_l2(addr, timeout):
    rc, out = run(["l2ping", "-c", "1", "-t", str(int(timeout)), addr],
                  timeout=timeout + 5)
    if rc == 0:
        return True
    low = out.lower()
    if any(h in low for h in PERMISSION_HINTS):
        raise DeviceError("l2ping has no permission. Run: sudo setcap "
                          "cap_net_raw+ep $(command -v l2ping)")
    if "command not found" in low:
        raise DeviceError("l2ping not found, install the bluez package")
    if any(h in low for h in ADAPTER_HINTS):
        raise DeviceError("Bluetooth adapter unavailable: %s" % out.strip())
    debug("l2ping:", out.strip() or rc)
    return False


def ping_ctl(addr):
    rc, out = run(["bluetoothctl", "info", addr])
    if rc == 127:
        raise DeviceError("bluetoothctl not found, install the bluez package")
    return "connected: yes" in out.lower()


def device_present(addr, method, timeout):
    if method == "bluetoothctl":
        return ping_ctl(addr)
    if method == "both":
        try:
            if ping_ctl(addr):
                return True
        except DeviceError:
            pass
    return ping_l2(addr, timeout)


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------
def monitor(settings):
    addr, env = settings.device, settings.env
    interval = settings.number("check_interval", 1)
    threshold = int(settings.number("fail_threshold", 1))
    timeout = settings.number("ping_timeout", 1)
    unlock_on_return = settings.flag("unlock_on_return")
    method = settings.setting("detection").strip().lower()
    if method not in DETECTION_METHODS:
        log("Unknown detection method %r, using l2ping" % method)
        method = "l2ping"

    session = Session(env)
    log("Bluetooth-Unlock active: device=%s env=%s detection=%s interval=%ss "
        "threshold=%d unlock_on_return=%s" % (addr, env, method, interval,
                                              threshold, unlock_on_return))

    misses = 0
    locked_by_us = False
    error_reported = None

    while True:
        started = time.monotonic()
        try:
            present = device_present(addr, method, timeout)
            if error_reported:
                log("Detection works again")
                error_reported = None
        except DeviceError as exc:
            # Never lock/unlock because detection itself is broken.
            if str(exc) != error_reported:
                log("ERROR:", exc)
                error_reported = str(exc)
            time.sleep(max(interval, 5))
            continue

        real_locked = session.is_locked()
        if real_locked is False and locked_by_us:
            # User unlocked manually (e.g. with password)
            debug("Session was unlocked manually")
            locked_by_us = False

        if present:
            if misses:
                debug("Device is back after %d miss(es)" % misses)
            misses = 0
            if locked_by_us:
                if unlock_on_return:
                    log("Device found, unlocking")
                    if session.unlock():
                        locked_by_us = False
                else:
                    locked_by_us = False
        else:
            misses += 1
            debug("Device not reachable (%d/%d)" % (misses, threshold))
            if misses >= threshold and not locked_by_us and real_locked is not True:
                log("Device gone, locking")
                if session.lock():
                    locked_by_us = True

        elapsed = time.monotonic() - started
        time.sleep(max(0.0, interval - elapsed))


def main():
    global DEBUG
    parser = argparse.ArgumentParser(description="Lock/unlock your session by Bluetooth device presence")
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG, help="path to config.ini")
    parser.add_argument("-s", "--setup", action="store_true", help="run setup again")
    parser.add_argument("-d", "--debug", action="store_true", help="verbose output")
    parser.add_argument("--device", help="Bluetooth address of the device")
    parser.add_argument("--env", choices=list(ENVIRONMENTS), type=str.upper,
                        help="lock method / desktop environment")
    args = parser.parse_args()
    DEBUG = args.debug

    settings = Settings(args.config)
    changed = False
    if args.device:
        if not MAC_RE.match(args.device.upper()):
            sys.exit("Invalid Bluetooth address: %s" % args.device)
        settings.set("DEVADDR", "devaddr", args.device.upper())
        changed = True
    if args.env:
        settings.set("DESKTOP", "env", args.env)
        changed = True
    if args.setup or not settings.device or not MAC_RE.match(settings.device):
        setup_device(settings)
        changed = True
    if args.setup or settings.env not in ENVIRONMENTS:
        setup_env(settings)
        changed = True
    if changed or settings.get("VERSION", "version") != CONFIG_VERSION:
        try:
            settings.save()
            log("Configuration saved to", settings.path)
        except OSError as exc:
            log("Could not save configuration:", exc)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        monitor(settings)
    except KeyboardInterrupt:
        print()
        log("Stopped")


if __name__ == "__main__":
    main()
