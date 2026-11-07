# Bluetooth-Unlock
Lock your Linux computer when your Bluetooth device (phone, watch...) goes away
and unlock it again when the device comes back.

How it works
------------
Every `check_interval` seconds the device is pinged with `l2ping` (or checked with
`bluetoothctl info`). When it is unreachable `fail_threshold` times in a row the
session is locked. When it is reachable again the session is unlocked
(if `unlock_on_return = yes`).

* A single lost ping does not lock the screen.
* If detection itself fails (no permission for `l2ping`, adapter switched off)
  the program does **not** lock/unlock, it only prints an error and keeps retrying.
* If you unlock manually with your password while the device is away,
  the session will be locked again (lock state is read from `loginctl`).

Installation
------------
1. Download the files.
2. Run `./install.sh` (installs `bluez` and allows `l2ping` to run without sudo).
3. Pair your device with the computer (recommended, makes detection more reliable).
4. Run `python3 Bluetooth-Unlock.py` and follow the setup (device + lock method).

Command line options
--------------------
```
python3 Bluetooth-Unlock.py [--setup] [--debug] [--device AA:BB:CC:DD:EE:FF]
                            [--env LOGINCTL|KDE|GNOME|XSCREENSAVER|MATE|CINNAMON]
                            [--config /path/to/config.ini]
```

Configuration (`config.ini`, next to the script)
------------------------------------------------
```
[DEVADDR]
devaddr = AA:BB:CC:DD:EE:FF

[DESKTOP]
env = LOGINCTL

[SETTINGS]
check_interval = 3     ; seconds between checks
fail_threshold = 3     ; consecutive misses before locking
ping_timeout = 2       ; l2ping timeout in seconds
unlock_on_return = yes ; unlock automatically when the device returns
detection = l2ping     ; l2ping | bluetoothctl | both
```

`detection = bluetoothctl` works without `l2ping` but requires the device to stay
connected to the computer (e.g. paired phone). `both` treats the device as present
if either method sees it.

Autostart (systemd user service)
--------------------------------
```
mkdir -p ~/.config/systemd/user
sed "s#@DIR@#$PWD#" bluetooth-unlock.service > ~/.config/systemd/user/bluetooth-unlock.service
systemctl --user daemon-reload
systemctl --user enable --now bluetooth-unlock.service
journalctl --user -u bluetooth-unlock -f     # logs
```
Run the setup once in a terminal before enabling the service.

Troubleshooting
---------------
* `l2ping has no permission` – run `sudo setcap cap_net_raw+ep $(command -v l2ping)`.
* Some phones do not answer `l2ping` while the screen is off – increase
  `fail_threshold`, or use `detection = both` with a paired, connected device.
* Run with `--debug` to see every check.
