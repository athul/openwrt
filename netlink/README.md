# Netlink HG323RGW on OpenWrt (realtek-luna)

Experimental OpenWrt support for the **Netlink HG323RGW** GPON ONT
(RTL9602C, 128 MiB RAM, 16 MiB SPI NOR, RTL8192FE 2.4 GHz radio), built on
the `realtek-luna/taroko` target of
[alphaonex86/openwrt](https://github.com/alphaonex86/openwrt).

It turns the ONT into a **dedicated IoT access point** behind a home router.
GPON is not used.

> ⚠️ Experimental, tested on one unit (board `HG323RW_V3.6`, factory HW
> `V3.7`, stock firmware `3.0.00-210529`). Make a full flash backup before
> writing anything, and keep a serial console connected.

## What works

| Area | Status |
|---|---|
| Boot | Stock U-Boot, unchanged. RAM boot over TFTP, or installed in flash (`k0`/`r0`) |
| Flash | Squashfs root plus a JFFS2 overlay in `r0`. U-Boot, env, `config` and `framework1` stay read-only |
| Upgrades | `sysupgrade` (writes `k0` and `r0`) |
| Ethernet | Two sockets split by the switch (`rtl9602c_eth.port_vlans=1`): port 1 (GE) = WAN `eth0.3`, port 0 (FE) = IoT `eth0.2` |
| Wi-Fi | One SSID, bridged with the FE socket into the isolated IoT network. Needs the unit's factory calibration (see `unit.json`) |
| LEDs | PON, LOS (red), GE LAN, FE LAN, FXS, WPS (green) as GPIO LEDs, plus WIFI. POWER is hardwired |
| LAN LED triggers | `swport0-link` / `swport1-link` follow each socket's link (selectable in LuCI) |
| Buttons | RST (reboot; factory reset after 5 s or more), WIFI (double press toggles the radio), PAIR (light show) |
| Extras | `netlink-ledd` status lamps, `netlink-lightshow`, LuCI, WireGuard/OpenVPN, WoL, adblock-fast |
| Not supported | GPON, the FXS phone port, a second SSID, red WPS / green LOS (not populated) |

### Hardware map (this board)

| Function | Pin |
|---|---|
| PON / LOS / FXS LEDs | GPIO 29 / 28 / 30 (active-low). PON/LOS pads are shared with LED-controller outputs 12/13 |
| FE / GE / WPS LEDs | GPIO 26 / 39 / 43 (active-low) |
| RST / WIFI / PAIR buttons | GPIO 38 / 37 / 41 (active-low, polled) |
| Avoid | GPIO 17 (UART0 TX pad) and GPIO 32 (resets the board) |

## Network design

```
home router (192.168.1.0/24) ── GE socket: wan = eth0.3, DHCP client
                                 FE socket + Wi-Fi "Netlink-IoT": iot = 10.67.67.1/24
```

- IoT devices may reach the internet only. Private ranges, the home LAN
  included, are rejected, and IPv6 forwarding is blocked.
- Towards the router, IoT devices get DHCP and DNS only.
- SSH (key-only) and LuCI (HTTPS) are allowed from private addresses on the WAN side.

## Building

The CPU is a Lexra-derived Realtek "Taroko" core. It has no
`lwl/lwr/swl/swr` instructions and has multiply errata the fork's GCC works around.
**Do not install official `mips_mips32` packages** (they can crash or
miscompute), and build everything from this tree instead.

```sh
./scripts/feeds update -a && ./scripts/feeds install -a
cp netlink/unit.example.json netlink/unit.json      # then fill in YOUR values
python3 netlink/prepare-overlay.py . ~/.ssh/id_ed25519.pub
cp netlink/netlink-flash.config .config && make defconfig
make -j"$(nproc)" KERNEL_PATCHVER=6.18
```

Images: `bin/targets/realtek-luna/taroko/*-squashfs-{kernel,rootfs,sysupgrade}.bin`.

`unit.json` is git-ignored. It holds the values that identify one physical
unit: the LAN MAC (case label), the Wi-Fi BSSID, and the radio's factory calibration
(`HW_WLAN0_*` from the `config` partition). Without calibration the radio
still works, but transmit power is uncalibrated.

## First install (from U-Boot, over serial + TFTP)

Stop autoboot at `9602C#`. Use the board's own `fl_*` variables, and **never type
offsets**: the bootloader lives on the same flash chip. **Never `saveenv`.**

```text
printenv fl_kernel1 fl_kernel1_sz fl_rootfs1 fl_rootfs1_sz   # 100000 400000 500000 800000
setenv serverip <tftp-server>; setenv ipaddr <board-ip>
tftpboot 0x82000000 kernel.bin;  crc32 ${fileaddr} ${filesize}
sf erase ${fl_kernel1} +${fl_kernel1_sz}
sf write ${fileaddr} ${fl_kernel1} ${filesize}
crc32 b4100000 ${filesize}                                     # must match
tftpboot 0x82000000 rootfs.bin;  crc32 ${fileaddr} ${filesize}
sf erase ${fl_rootfs1} +${fl_rootfs1_sz}
sf write ${fileaddr} ${fl_rootfs1} ${filesize}
crc32 b4500000 ${filesize}                                     # must match
boot
```

Afterwards, upgrade with `sysupgrade` (add `-n` only to discard settings).
Then set a root password (`passwd`) and the Wi-Fi key
(`uci set wireless.default_radio0.key=...; uci commit wireless; wifi`).

**Restoring stock:** write your backed-up `k0`/`r0` the same way.

## Front panel tools (`netlink/`)

| File | Installed as | Purpose |
|---|---|---|
| `prepare-overlay.py` | (build-time) | Writes `files/`: network, firewall, Wi-Fi, calibration service, tools |
| `netlink-ledd` (+ `.init`) | `/usr/bin`, `/etc/init.d` | PON = internet up (and flickers with WAN traffic), LOS = internet down, WPS blinks for new Wi-Fi clients, FXS heartbeat. Configure in `/etc/config/netlink_ledd` (`'none'` disables a role) |
| `netlink-lightshow` | `/usr/bin` | About 35 s light show on all seven lamps. Restores the LEDs on exit or Ctrl-C |
| `rc.button-rfkill` | `/etc/rc.button/rfkill` | WIFI button: a double press within 1.5 s toggles the radios |
| `rc.wps-lightshow` | `/etc/rc.wps/50-netlink-lightshow` | PAIR button: light show (ignored for the first 90 s after boot) |
| `netlink-flash.config` | (build seed) | Device, squashfs, NOR write, LuCI, LED triggers, VPN, WoL, adblock |
