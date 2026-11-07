#!/bin/bash
# Installs dependencies for Bluetooth-Unlock and allows l2ping to run without sudo.
set -u

echo "============================================="
echo "Bluetooth Unlock Tool For Linux Distributions"
echo "============================================="

SUDO=""
if [[ "$EUID" -ne 0 ]]; then
  SUDO="sudo"
fi

if command -v apt-get >/dev/null 2>&1; then
  echo "Using apt"
  $SUDO apt-get update
  $SUDO apt-get install -y python3 bluez libcap2-bin || exit 1
elif command -v dnf >/dev/null 2>&1; then
  echo "Using dnf"
  $SUDO dnf install -y python3 bluez libcap || exit 1
elif command -v yum >/dev/null 2>&1; then
  echo "Using yum"
  $SUDO yum install -y python3 bluez libcap || exit 1
elif command -v pacman >/dev/null 2>&1; then
  echo "Using pacman"
  $SUDO pacman -S --needed --noconfirm python bluez bluez-utils libcap || exit 1
elif command -v zypper >/dev/null 2>&1; then
  echo "Using zypper"
  $SUDO zypper install -y python3 bluez libcap-progs || exit 1
elif command -v emerge >/dev/null 2>&1; then
  echo "Using emerge"
  $SUDO emerge --noreplace dev-lang/python net-wireless/bluez sys-libs/libcap || exit 1
else
  echo "Unsupported package manager, please install: python3, bluez (l2ping, bluetoothctl), libcap (setcap)"
fi

L2PING="$(command -v l2ping || true)"
if [[ -z "$L2PING" ]]; then
  echo "WARNING: l2ping not found. On some distributions it is in a separate package"
  echo "(e.g. bluez-deprecated / bluez-utils). Alternatively set 'detection = bluetoothctl' in config.ini."
else
  # So that there is no need to use sudo in the program
  $SUDO setcap 'cap_net_raw,cap_net_admin+eip' "$L2PING" && echo "Capabilities set on $L2PING"
fi

echo ""
echo "Install complete! Run: python3 Bluetooth-Unlock.py"
echo "To start automatically on login see README.md (systemd user service)."
