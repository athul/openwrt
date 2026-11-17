# OpenWrt on the Netlink HG323RGW: a from-scratch guide

This guide takes a stock **Netlink HG323RGW** GPON ONT (the router many Indian ISPs
hand out) and turns it into an **OpenWrt** router or access point. It covers:
- opening the box and wiring up a serial console;
- backing up the original firmware;
- building OpenWrt;
- testing it **from RAM** without touching flash;
- installing it **to flash**;
- upgrading, adding packages, and using the LEDs and buttons;
- recovering, including restoring the stock firmware.

> ⚠️ **Experimental.** This is community work on an unofficial OpenWrt target, verified
> on one unit (board `HG323RW_V3.6`, factory hardware `V3.7`, stock firmware
> `3.0.00-210529`). Your unit may differ. You can brick it, and you'll lose GPON (fibre)
> and the phone port. Keep the serial console connected and **make the backup** before
> writing anything. Nothing here is supported by Netlink, your ISP or the OpenWrt project.

---

## Contents

1. [What you get, and what you don't](#1-what-you-get-and-what-you-dont)
2. [Hardware overview](#2-hardware-overview)
3. [What you need](#3-what-you-need)
4. [Serial console (UART)](#4-serial-console-uart)
5. [U-Boot basics and the golden rules](#5-u-boot-basics-and-the-golden-rules)
6. [Back up the stock flash](#6-back-up-the-stock-flash)
7. [Read your unit's MAC and Wi-Fi calibration](#7-read-your-units-mac-and-wi-fi-calibration)
8. [Build OpenWrt](#8-build-openwrt)
9. [Test from RAM (nothing written)](#9-test-from-ram-nothing-written)
10. [Install to flash](#10-install-to-flash)
11. [First setup](#11-first-setup)
12. [Upgrading](#12-upgrading)
13. [Adding packages ("plugins")](#13-adding-packages-plugins)
14. [LEDs](#14-leds)
15. [Buttons](#15-buttons)
16. [Network layout and firewall](#16-network-layout-and-firewall)
17. [Recovery and going back to stock](#17-recovery-and-going-back-to-stock)
18. [Troubleshooting and known issues](#18-troubleshooting-and-known-issues)
19. [Reference](#19-reference)

---

## 1. What you get, and what you don't

| Works | Status |
|---|---|
| Boot | Stock U-Boot, unchanged. OpenWrt runs from RAM (TFTP) or from flash |
| Flash | Squashfs root plus a writable JFFS2 overlay (settings and packages survive reboots and upgrades) |
| Upgrades | Standard `sysupgrade`, from the shell or LuCI |
| Ethernet | Both sockets, each its own interface: **GE = `eth0.3`**, **FE = `eth0.2`** |
| Wi-Fi | 2.4 GHz (RTL8192FE), one SSID, WPA2, with your unit's factory calibration |
| LEDs | PON, LOS (red), GE, FE, FXS, WPS (green) and WIFI are all controllable. POWER is hardwired |
| Buttons | RST, WIFI and PAIR |
| Web UI | LuCI over HTTPS |
| Kernel | Linux 6.18 |

**Not supported:**
- **GPON/fibre** (it's disabled; this becomes an Ethernet device behind another router);
- the **FXS phone port**;
- **more than one SSID**;
- 5 GHz (no such radio).

**Good uses:**
- a dedicated **IoT access point** (this guide's default layout);
- a small **wired router** or **Wi-Fi extender**;
- a WireGuard or OpenVPN endpoint for light traffic.

**Not a good fit:** heavy Wi-Fi traffic. Throughput is modest, and a known Wi-Fi TX stall is
mitigated rather than fixed (see §18).

---

## 2. Hardware overview

| Part | Detail |
|---|---|
| SoC | Realtek **RTL9602C** ("Taroko"), 625 MHz, Lexra-derived MIPS-like core |
| RAM | 128 MiB DDR2 |
| Flash | 16 MiB SPI NOR |
| Wi-Fi | Realtek **RTL8192FE** (PCIe), 2.4 GHz 2×2 |
| Ethernet | 2 sockets on the internal switch: **GE** (gigabit) and **FE** (100 Mbit/s) |
| Bootloader | Realtek U-Boot, prompt `9602C#` |
| Console | 3.3 V UART, **115200 8N1** |

### The CPU is not a normal MIPS32

The Taroko core **lacks the `lwl`/`lwr`/`swl`/`swr` instructions** and has
multiply-unit errata. The fork's compiler works around both. This has three consequences:
- **Never install packages from the official OpenWrt `mips_mips32` feeds.** They may crash
  or, worse, compute wrong results. Build every package from the same tree (§13).
- **Go programs don't run** (Tailscale, for example). Go's runtime uses the missing
  instructions. Run Tailscale on another machine on your LAN instead.
- Rust and other prebuilt binaries are risky for the same reason.

### Flash layout (factory partitions, unchanged by this install)

| # | Name | Offset | Size | Contents | Written by OpenWrt? |
|---|---|---|---|---|---|
| 0 | `boot` | `0x000000` | 256 KiB | U-Boot | **Never** |
| 1 | `env` | `0x040000` | 8 KiB | U-Boot environment | **Never** |
| 2 | `env2` | `0x042000` | 8 KiB | Environment copy | **Never** |
| 3 | `config` | `0x044000` | 752 KiB | Factory data: MAC, Wi-Fi calibration, serials | **Never** (read-only) |
| 4 | `k0` | `0x100000` | 4 MiB | Kernel | Yes |
| 5 | `r0` | `0x500000` | 8 MiB | Root filesystem + overlay | Yes |
| 6 | `framework1` | `0xd00000` | 3 MiB | Unused (all `0xff` on the test unit) | Never |

U-Boot always boots the kernel at `k0` (flash address `0xb4100000`) and passes
`root=31:5` (`r0`). OpenWrt keeps that arrangement, so **U-Boot and its environment are
never changed**. Inside `r0`, OpenWrt puts its squashfs image first, then a JFFS2 area
(`rootfs_data`, about 4–5 MiB) for your settings and packages.

---

## 3. What you need

**Hardware:**
- A **3.3 V USB-to-UART adapter** (CP2102, CH340, FT232 and so on). An ESP32 or ESP8266
  dev board also works (see §4.3). **Not** a 5 V or RS-232 adapter.
- Jumper wires; header pins and a soldering iron if your board has bare holes.
- A multimeter (strongly recommended).
- **One Ethernet cable** (enough for everything below), and a small Phillips screwdriver
  or opening tool.

**A computer:**
- **Linux** for building OpenWrt (a VM or WSL2 works, as does an x86 machine). Allow
  around 15–20 GB of disk and an hour for the first build.
- A terminal program: `tio`, `picocom`, `minicom` or PuTTY.
- A **TFTP server**: `tftpd-hpa`, `dnsmasq`, or Tftpd64 on Windows.

**Software:** the OpenWrt fork carrying the Netlink support, branch **`netlink`** at
`github.com/athul/openwrt`. It's based on
[`alphaonex86/openwrt`](https://github.com/alphaonex86/openwrt), which provides the
`realtek-luna` target.

---

## 4. Serial console (UART)

### 4.1 Open the case and find the header

1. Unplug power. Remove the screws under the feet/labels and unclip the shell.
2. On the board, look next to the large **heatsink** (the SoC) for a row of **4
   pads or holes** labelled **`3.3`, `GND`, `RX`, `TX`**. The order depends on how you
   hold the board, so **follow the printed labels**.
3. Solder a 4-pin header, or hold jumper wires in the holes firmly.

### 4.2 Check the pins with a multimeter (router powered)

- **GND:** with the router unplugged, continuity (beep) to the **outer contact of the DC
  jack**.
- **3.3:** about 3.3 V to GND. **Don't connect this pin** to your adapter.
- **TX:** about 3.3 V at idle, flickering during boot.
- **RX:** about 3.3 V.

Use the DC volts range that covers 3.3 V (for example 20 V). If you read odd voltages such as 10 V, your **ground is
wrong**: recheck it before connecting anything.

### 4.3 Wire it

**Standard USB-UART adapter** (crossed lines):

```
Router GND  ── Adapter GND
Router TX   ── Adapter RX
Router RX   ── Adapter TX
Router 3.3  ── (not connected)
```

**ESP32 dev board used as a plain USB-UART** (its CP2102 chip, no sketch):
- Hold the ESP32 in reset by connecting **`EN` to `GND`**, so its own CPU stays off the
  lines.
- Then connect **straight through**, because the board's labels name the ESP32's own pins:

```
Router GND ── GND
Router TX  ── TX0 (GPIO1)
Router RX  ── RX0 (GPIO3)
            EN ── GND   (jumper on the ESP32 board)
```

If you see Wi-Fi scan messages in the console, the ESP32 is still running: fix the
`EN` jumper.

**Never power the router from the UART adapter.** Always power off both sides before
changing wires. It's a good idea to connect only **TX + GND** first, to read output safely,
and add RX once you see the boot log.

### 4.4 Open the console

```sh
# Linux (adapter is usually /dev/ttyUSB0); macOS: /dev/cu.usbserial-XXXX
tio -b 115200 -d 8 -p none -s 1 -f none /dev/ttyUSB0
# or: picocom -b 115200 /dev/ttyUSB0
```

Power the router on. You should see the U-Boot banner, then the stock Linux boot.

### 4.5 Serial is lossy: how to type

The console **drops or merges characters**, especially when you paste or type long lines.
This is the single biggest source of trouble.

- **Type U-Boot commands by hand**, a short line at a time. Don't paste.
- **Check the echo** of every command before pressing Enter, and check the result (for
  example `printenv bootargs`) before a command that acts on it.
- Once OpenWrt is running, **switch to SSH** for anything longer than a line. For a
  multi-line job, serve a script over HTTP and run `wget -qO- http://<pc>/x.sh | sh`.
- If the console goes silent, unplug and replug the adapter and restart the terminal.

---

## 5. U-Boot basics and the golden rules

Power-cycle with the console open and **press any key** during:

```
Hit any key to stop autoboot:  1
```

You have about 1 second, so tap a key repeatedly while powering on. You'll land at:

```
9602C#
```

**Commands used in this guide:** `printenv`, `setenv`, `tftpboot`, `bootm`, `crc32`,
`sf erase`, `sf write`, `boot`, `help`.

### Golden rules

1. **Never run `saveenv`.** Every `setenv` here is temporary and disappears at the next
   power cycle. That's your safety net.
2. **Never type flash offsets.** The board's environment already holds them as variables:
   `fl_kernel1`, `fl_kernel1_sz`, `fl_rootfs1` and `fl_rootfs1_sz`. One typo in a
   hand-typed offset can erase U-Boot. Check them first:
   ```
   printenv fl_kernel1 fl_kernel1_sz fl_rootfs1 fl_rootfs1_sz
   ```
   Expected: `100000`, `400000`, `500000`, `800000`. **If yours differ, stop**: this guide
   doesn't match your unit.
3. **Don't run the vendor upgrade helpers** in the environment (`upb`, `upk`, `upr`,
   `upt`, `yu`). They erase flash.
4. **Check CRCs** after every TFTP transfer and every flash write.

---

## 6. Back up the stock flash

**Do this first. It's your only way back to stock.** It also preserves your unit's factory
data (MAC, Wi-Fi calibration).

The stock firmware's login password is unknown, so boot it into a bare root shell instead.
Nothing is changed, since `saveenv` isn't used.

### 6.1 Get a root shell on the stock firmware

At `9602C#`:

```
setenv bootargs_base 'console=ttyS0,115200 init=/bin/sh'
boot
```

You'll get a `#` prompt. This shell **is PID 1**: **never type `exit`**. Then:

```sh
mount -t proc proc /proc
cat /proc/mtd            # should list boot env env2 config k0 r0 framework1
```

### 6.2 Bring up Ethernet in the minimal shell

Connect your PC to one of the router's sockets. On this unit, **GE** appeared as
`eth0.3`; if nothing links up, try the other socket.

```sh
ifconfig eth0 up
ifconfig eth0.2 up
ifconfig eth0.3 192.168.50.1 netmask 255.255.255.0 up
/bin/diag port set phy-force-power-down port all state disable   # powers up the PHYs
```

On the PC, give the wired interface a fixed address on the same network:

```sh
sudo ip addr add 192.168.50.2/24 dev <your-eth>
ping -c 3 192.168.50.1
```

> On Linux with NetworkManager, the manager may remove a manually added address. Either
> set the interface unmanaged for the test (`nmcli device set <your-eth> managed no`,
> then `managed yes` afterwards) or make a static profile. The same applies in §9.

Periodic `RT_ERR_RG_NOT_INIT` messages in the console are vendor noise and can be ignored.

### 6.3 Stream the flash to your PC

On the router, first record the checksum:

```sh
cat /dev/mtd0ro /dev/mtd1ro /dev/mtd2ro /dev/mtd3ro /dev/mtd4ro /dev/mtd5ro /dev/mtd6ro | md5sum
```

Then start a sender:

```sh
cat /dev/mtd0ro /dev/mtd1ro /dev/mtd2ro /dev/mtd3ro /dev/mtd4ro /dev/mtd5ro /dev/mtd6ro | nc -l -p 9000
```

On the PC, **after** the router is listening (OpenBSD netcat; note the `-d`):

```sh
nc -d -w 15 192.168.50.1 9000 | head -c 16777216 > full-flash.bin
ls -l full-flash.bin        # must be exactly 16777216 bytes
md5sum full-flash.bin       # must equal the router's md5sum
```

Without `-d` the transfer can end empty with `nc: short write` on the router.

### 6.4 Split it into partitions

```sh
dd if=full-flash.bin of=0-boot.bin       bs=1 skip=$((0x000000)) count=$((0x040000))
dd if=full-flash.bin of=1-env.bin        bs=1 skip=$((0x040000)) count=$((0x002000))
dd if=full-flash.bin of=2-env2.bin       bs=1 skip=$((0x042000)) count=$((0x002000))
dd if=full-flash.bin of=3-config.bin     bs=1 skip=$((0x044000)) count=$((0x0bc000))
dd if=full-flash.bin of=4-k0.bin         bs=1 skip=$((0x100000)) count=$((0x400000))
dd if=full-flash.bin of=5-r0.bin         bs=1 skip=$((0x500000)) count=$((0x800000))
dd if=full-flash.bin of=6-framework1.bin bs=1 skip=$((0xd00000)) count=$((0x300000))
crc32 4-k0.bin 5-r0.bin      # write these down: you'll compare against them in U-Boot
```

(`crc32` comes from `libarchive-zip-perl`; `python3 -c "import zlib,sys;print(hex(zlib.crc32(open(sys.argv[1],'rb').read())))" 4-k0.bin` works too.)

**Keep several copies** (PC, cloud, USB stick). The backup contains **private data**: your
MAC, serial numbers, and possibly ISP/PON credentials and stock Wi-Fi passwords. **Never
publish it.**

Power-cycle to return to normal stock boot.

### 6.5 Rehearse reading flash in U-Boot (optional, recommended)

At `9602C#`, run read-only:

```
crc32 b4100000 400000
crc32 b4500000 800000
```

The results must equal `crc32 4-k0.bin` and `crc32 5-r0.bin` from your backup. This proves
your backup matches flash, and practises the commands you'll use later.

---

## 7. Read your unit's MAC and Wi-Fi calibration

Each unit stores its own identity in the `config` partition (Realtek XML inside JFFS2):
- **`ELAN_MAC_ADDR`:** the LAN MAC, also printed on the case label.
- **`HW_WLAN0_*`:** the radio's **factory calibration** (TX power per channel, thermal,
  crystal trim, regulatory domain). The RTL8192FE's own efuse is blank on these boards, so
  without these values the radio works but its transmit power is uncalibrated.

The fork includes a reader, `package/utils/gpon-provision/src/rtk_factory.c`. Build a copy
for your PC (it needs zlib) and run it on your backed-up `3-config.bin`. It only reads the
file. Its buffer is sized for 512 KiB, but this board's `config` partition is 752 KiB, so
raise the buffer to 1 MiB while compiling:

```sh
sed 's/#define PART_MAX (512 \* 1024)/#define PART_MAX (1024 * 1024)/' \
    package/utils/gpon-provision/src/rtk_factory.c > /tmp/rtk_factory.c
gcc -O2 -o rtk_factory /tmp/rtk_factory.c -lz
./rtk_factory -p "$PWD/3-config.bin" mac              # LAN MAC: must match the case label
./rtk_factory -p "$PWD/3-config.bin" wlan_cck_a
./rtk_factory -p "$PWD/3-config.bin" wlan_cck_b
./rtk_factory -p "$PWD/3-config.bin" wlan_ht40_a
./rtk_factory -p "$PWD/3-config.bin" wlan_ht40_b
./rtk_factory -p "$PWD/3-config.bin" wlan_thermal
./rtk_factory -p "$PWD/3-config.bin" wlan_xcap
./rtk_factory -p "$PWD/3-config.bin" wlan_reg_domain
```

This builds and runs on Linux and macOS (tested on macOS with the system compiler).
`./rtk_factory -p "$PWD/3-config.bin" all` prints every field it knows; keep that output
private.

Notes:
- The four power arrays are **14 hex bytes** each (one per channel). The tool prints them
  without a prefix (for example `26,26,…`, meaning `0x26`). In `unit.json`, write each
  byte with `0x` in front (`0x26,0x26,…`): the driver needs the prefix.
- `thermal`, `xcap` and `reg_domain` are **decimal** integers.
- The stored **Wi-Fi MAC** usually has a generic Realtek prefix (`00:e0:4c:…`). Use
  **LAN MAC + 1** as the Wi-Fi MAC instead.

---

## 8. Build OpenWrt

### 8.1 Get the source

```sh
git clone -b netlink https://github.com/athul/openwrt.git
cd openwrt
./scripts/feeds update -a
./scripts/feeds install -a
```

Install the usual OpenWrt build prerequisites for your distribution (see the OpenWrt
"Build system setup" wiki page): gcc, g++, make, ncurses-dev, zlib-dev, openssl-dev,
python3, perl, rsync, unzip, file, wget, gawk, bison, flex, gettext and so on.

> **NixOS:** use an FHS environment (`buildFHSEnv`) containing those tools, with
> `FAKEROOTDONTTRYCHOWN=1` and `LC_ALL=C` set in its profile.

### 8.2 Describe your unit: `netlink/unit.json`

```sh
cp netlink/unit.example.json netlink/unit.json
$EDITOR netlink/unit.json
```

| Key | Value |
|---|---|
| `lan_mac` | The MAC from the label / `rtk_factory … mac` |
| `wifi_mac` | `lan_mac` + 1 |
| `country` | Your ISO country code (for example `IN`), which sets the legal Wi-Fi limits |
| `wifi_cal` | The seven calibration values from §7 |
| `iot_controller_ip` | Optional: the one LAN host (for example a Home Assistant server) allowed to reach IoT devices. Default: the whole home LAN |
| `luci_iot_mac` | Optional: lets one IoT-side host open LuCI |

`unit.json` is **git-ignored on purpose**. It identifies one physical device; don't
commit or publish it. Without it the build still works, but you get the shared default MAC
and uncalibrated Wi-Fi.

### 8.3 Generate the configuration overlay

```sh
python3 netlink/prepare-overlay.py . ~/.ssh/id_ed25519.pub
```

This writes `files/` (copied into the image as the router's first-boot configuration):
- the network, DHCP and firewall layout (§16);
- the Wi-Fi setup, with the radio on but **no key**;
- the calibration loader (`/etc/init.d/netlink-wifi-cal`);
- the front-panel tools (§14–15);
- **your SSH public key**: the router allows **key-only** SSH.

The SSH key argument is required. Generate one first if you have none:
`ssh-keygen -t ed25519`.

### 8.4 Configure and build

```sh
cp netlink/netlink-flash.config .config
make defconfig
make download -j4
make -j"$(nproc)" KERNEL_PATCHVER=6.18
```

The first build takes 30–90 minutes. If it fails, rerun with `make -j1 V=s` and read the
**first** error. The many "recursive dependency" warnings are normal.

**Before flashing, verify the result:**

```sh
grep NOR_WRITE build_dir/target-*/linux-*/linux-*/.config     # must say CONFIG_MTD_LUNA_NOR_WRITE=y
ls -l bin/targets/realtek-luna/taroko/
```

- `…-squashfs-kernel.bin` must be **≤ 4,194,304 bytes** (the `k0` size).
- `…-squashfs-rootfs.bin` must be **≤ about 7 MiB**, to leave room for the overlay (hard
  limit 8 MiB).
- `…-squashfs-sysupgrade.bin` must list both `kernel` and `root` in
  `tar tvf …-sysupgrade.bin`.

The size check in the build isn't fatal, so check the sizes yourself.

### 8.5 A RAM-test image (read-only)

To test without risk, build a second variant that boots entirely from RAM and **cannot
write flash**. Change three lines in `.config` and rebuild:

```
CONFIG_TARGET_ROOTFS_INITRAMFS=y
# CONFIG_TARGET_ROOTFS_SQUASHFS is not set
# CONFIG_KERNEL_MTD_LUNA_NOR_WRITE is not set
```

```sh
make defconfig && make target/linux/clean && make -j"$(nproc)" KERNEL_PATCHVER=6.18
grep NOR_WRITE build_dir/target-*/linux-*/linux-*/.config       # must be "is not set"
```

The image is the `…initramfs…` kernel file in `bin/targets/realtek-luna/taroko/`. Copy it
somewhere safe.

> ⚠️ **Switching back:** after a RAM build, run **`make target/linux/clean`** before the flash
> build. Otherwise the old kernel config can survive and your "flash" image can't write
> flash, so overlays and upgrades fail.

---

## 9. Test from RAM (nothing written)

This boots OpenWrt from RAM using the stock U-Boot. A power cycle returns to whatever is
in flash.

### 9.1 TFTP server on your PC

Put the image in the TFTP directory and serve it. Example with `tftpd-hpa`:

```sh
mkdir -p /srv/tftp && cp openwrt-*initramfs*.bin /srv/tftp/owrt-ram.bin
sudo in.tftpd -L -s -a <pc-ip>:69 /srv/tftp
```

Allow UDP 69 and the data ports through your PC's firewall.

### 9.2 Pick an address plan

- **Direct cable (simplest):** PC ↔ router socket. The PC is `192.168.50.2/24` and the
  router `192.168.50.1`.
- **Through your home network** (if you only have one cable and it must stay on the home
  router): give U-Boot an unused address on the home LAN **outside the DHCP pool**, with
  `serverip` = your PC's LAN address. The router's socket goes to the home router.

### 9.3 Boot it

At `9602C#`, typed by hand:

```
setenv ipaddr 192.168.50.1
setenv serverip 192.168.50.2
setenv bootargs console=ttyS0,115200
printenv ipaddr serverip bootargs
tftpboot 0x82000000 owrt-ram.bin
crc32 ${fileaddr} ${filesize}
bootm 0x82000000
```

- `Bytes transferred` must equal the file size. Repeated `T` characters mean a timeout:
  check the cable, addresses and PC firewall.
- The board-specific kernel arguments (`rtl9602c_eth.port_vlans=1 rtl9602c_eth.hw_nat=0`)
  come from the device tree and are merged automatically. You don't type them.

### 9.4 What to check in the RAM image

Once at the OpenWrt prompt (press Enter):

```sh
uname -r                                   # 6.18.x
dmesg | grep -o "SPI-NOR, read-[a-z]*"     # read-only in the RAM image
cat /sys/module/rtl9602c_eth/parameters/port_vlans    # 1
ip -br addr                                # br-iot 10.67.67.1, wan (eth0.3)
logread | grep netlink-wifi-cal            # "unit calibration loaded"
ls /sys/class/leds                         # the panel LEDs (§14)
```

Then:
1. Plug the **GE** socket into your home router. `wan` gets an address, and SSH with your
   key works from the home LAN.
2. Join Wi-Fi after setting a temporary key (§11).
3. Press the buttons (§15).

Run **three clean cycles** (catch U-Boot → TFTP → boot) before you erase anything. That
proves your serial and TFTP setup is reliable enough to recover with.

---

## 10. Install to flash

**Checklist before you start:**
- the backup (§6) is verified and you have **`4-k0.bin` and `5-r0.bin` in your TFTP
  folder**, ready to restore stock;
- the flash images passed §8.4;
- RAM booting works reliably (§9).

Copy the two flash images into the TFTP folder with short names:

```sh
cp …-squashfs-kernel.bin /srv/tftp/nl-k0.bin
cp …-squashfs-rootfs.bin /srv/tftp/nl-r0.bin
```

At `9602C#` (type each line by hand and check the echo):

```
setenv ipaddr 192.168.50.1
setenv serverip 192.168.50.2
printenv fl_kernel1 fl_kernel1_sz fl_rootfs1 fl_rootfs1_sz
```

Expected: `100000` `400000` `500000` `800000`. **Stop if not.**

**Kernel → `k0`:**

```
tftpboot 0x82000000 nl-k0.bin
crc32 ${fileaddr} ${filesize}
sf erase ${fl_kernel1} +${fl_kernel1_sz}
sf write ${fileaddr} ${fl_kernel1} ${filesize}
crc32 b4100000 ${filesize}
```

The two `crc32` results must match.

**Root filesystem → `r0`:**

```
tftpboot 0x82000000 nl-r0.bin
crc32 ${fileaddr} ${filesize}
sf erase ${fl_rootfs1} +${fl_rootfs1_sz}
sf write ${fileaddr} ${fl_rootfs1} ${filesize}
crc32 b4500000 ${filesize}
```

These two must match as well.

Then type `boot`. Don't `saveenv`.

**Note on `${filesize}` in the second `crc32`:** it's the size of the file you just
transferred. Run the flash `crc32` **before** the next `tftpboot`, as shown above.

**If a CRC doesn't match:** don't boot. Repeat the transfer, erase and write for that
partition. If it keeps failing, restore stock (§17).

### First flash boot: what to expect

- `luna-spi-nor … read-write` in the log.
- The first boot formats the JFFS2 overlay in the background. Give it a minute, then check:

```sh
mount | grep -E " /rom | /overlay "     # /rom squashfs, /overlay jffs2
cat /proc/mtd                            # rootfs_data appears; framework1 shifts to mtd7 (harmless)
```

- To test that settings persist: `touch /etc/persist-test`, then pull the power. The file
  must still be there after the cold boot.

From now on the router **boots OpenWrt by itself**, and you can do almost everything over
SSH. Keep the serial wiring for emergencies.

---

## 11. First setup

Connect the **GE** socket to your home router (it gets an address by DHCP on `wan`).
Find the address in the home router's client list, or run `ip -br addr` on the serial
console. Reserving a fixed address for it on the home router helps.

**SSH (key-only):**

```sh
ssh root@<router-ip>
```

**Set a root password** (needed for LuCI, and on the serial console):

```sh
passwd
```

**Wi-Fi.** The image ships the radio on but with **no key**, so nobody can join until you
set one:

```sh
uci set wireless.default_radio0.key='<your-wifi-password>'   # 8–63 characters
uci set wireless.default_radio0.ssid='<your-ssid>'          # optional; default "Netlink-IoT"
uci commit wireless && wifi
```

**LuCI:** `https://<router-ip>`. Accept the self-signed certificate and log in as root
with your new password. LuCI and SSH are allowed **from the WAN side's private
addresses** (your home LAN), never from IoT devices.

**Time and country:** NTP is on by default. Check that the Wi-Fi country under LuCI →
Network → Wireless matches your location.

**Back up your configuration:**

```sh
sysupgrade -b /tmp/backup.tar.gz     # then copy it off: ssh root@<ip> "cat /tmp/backup.tar.gz" > backup.tar.gz
```

Or use LuCI → System → Backup / Flash Firmware. The backup contains password hashes and
the Wi-Fi key, so keep it private.

---

## 12. Upgrading

Build a new image (§8), then copy the **sysupgrade** file to the router. Dropbear has no
SFTP, so pipe it over SSH:

```sh
ssh root@<router-ip> "cat > /tmp/fw.bin" < …-squashfs-sysupgrade.bin
ssh root@<router-ip> "sysupgrade -T /tmp/fw.bin && echo OK"   # test only
ssh root@<router-ip> "sysupgrade -v /tmp/fw.bin"              # keep settings
```

- Run `sysupgrade` **in the foreground**. A backgrounded one dies with the SSH session.
  The `ubus … Connection failed` message at the end is normal: SSH closes as the upgrade
  starts. The router is back in about 2 minutes.
- **`-n`** discards all settings (a factory-fresh install).
- **What's kept:** `/etc/config/*`, `/etc/shadow` (passwords), `/etc/dropbear` (keys), the
  LuCI certificate, and anything in `/etc/sysupgrade.conf`.
- **What's lost:** files you changed by hand in `/usr/bin` or elsewhere, and packages
  installed at runtime. Put permanent changes in the source tree or `files/` and rebuild.

You can also upload the sysupgrade file in LuCI → System → Backup / Flash Firmware.

---

## 13. Adding packages ("plugins")

### Why not `apk add` from the internet?

The official OpenWrt package feeds are built for normal MIPS32 CPUs. **Don't install them
on this board** (§2). Packages come from **your own build tree** instead.

### Add packages into the image (recommended)

```sh
make menuconfig      # tick packages with <*> (built in) and save
# or append lines to .config, e.g.
echo 'CONFIG_PACKAGE_luci-app-nlbwmon=y' >> .config && make defconfig
make -j"$(nproc)" KERNEL_PATCHVER=6.18
```

Then sysupgrade (§12). Built-in packages survive upgrades automatically.

Watch the **rootfs size** (≤ about 7 MiB, §8.4). Every package eats the overlay space left
for settings. Check with `df -h /overlay` on the router.

### Already in the default image

| Package | What for |
|---|---|
| `luci-ssl` | Web UI over HTTPS |
| `luci-mod-dashboard` | Dashboard view in LuCI |
| `luci-proto-wireguard`, `kmod-wireguard`, `wireguard-tools` | WireGuard VPN |
| `luci-proto-openvpn`, `openvpn-mbedtls` | OpenVPN |
| `luci-app-wol`, `etherwake` | Wake-on-LAN for your PCs |
| `kmod-leds-gpio`, `kmod-ledtrig-*` | LED drivers and triggers |
| `iperf3` | Throughput testing |

### Good candidates to add

| Package | Use |
|---|---|
| `luci-app-nlbwmon` | Bandwidth per device |
| `luci-app-ddns` | Dynamic DNS for VPN access |
| `luci-app-statistics` | Graphs (heavier) |
| `luci-app-adblock-fast` | DNS ad and tracker blocking |
| `tcpdump-mini` | Packet captures |

Avoid anything written in Go (Tailscale, AdGuard Home and so on, see §2) and large apps:
there's only 128 MiB of RAM and a few MiB of flash.

### Installing a single package at runtime (advanced)

Packages built in **the same tree** are compatible. Build one, copy its `.apk` from
`bin/packages/…` to the router, then `apk add --allow-untrusted /tmp/<pkg>.apk`. Runtime
installs are **lost on sysupgrade**, so prefer building them in.

---

## 14. LEDs

### Map

All GPIO LEDs are **active-low**.

| Panel label | Linux LED name | Pin | Default behaviour |
|---|---|---|---|
| POWER | — | hardwired | Always on |
| PON | `green:pon` | GPIO 29 | Internet up; flickers with WAN traffic |
| LOS | `red:los` | GPIO 28 | Internet down (red only; no green LED is fitted) |
| GE | `green:lan-ge` | GPIO 39 | GE socket link, flickers with traffic |
| FE | `green:lan-fe` | GPIO 26 | FE socket link, flickers with traffic |
| FXS | `green:fxs` | GPIO 30 | Heartbeat |
| WPS | `green:wps` | GPIO 43 | Blinks when a new Wi-Fi client joins (no red LED is fitted) |
| WIFI | `rtl8192fe-phy0` | Wi-Fi chip | Wi-Fi activity |

The LOS LED is also wired to the optical module's "loss of signal" line, so it may light
briefly at power-on before Linux takes over.

### Control by hand

```sh
ls /sys/class/leds
cat /sys/class/leds/green:fxs/trigger                 # [current] trigger is in brackets
echo none > /sys/class/leds/green:fxs/trigger
echo 1 > /sys/class/leds/green:fxs/brightness         # on
echo 0 > /sys/class/leds/green:fxs/brightness         # off
echo timer > /sys/class/leds/green:fxs/trigger        # blink
```

Useful triggers include `none`, `default-on`, `timer`, `heartbeat`, `netdev` (link and
traffic of an interface), `phy0tx`/`phy0rx`, `swport0-link`/`swport1-link`, `pattern`
and `oneshot`.

### Persistent LED settings: LuCI

Go to **LuCI → System → LED Configuration**, then add or edit an entry: pick the LED
and a trigger.

- **FE/GE sockets:** use **`swport0-link`** (FE) or **`swport1-link`** (GE). These follow
  each physical socket's link from the switch.
  - Don't use `netdev` on `eth0`: `eth0` is shared by both sockets, so both LEDs would
    light.
  - Traffic flicker is built into the `swport` triggers. Turn it off with
    `echo 0 > /sys/module/rtl9602c_eth/parameters/port_led_activity`.
- **Wi-Fi traffic on any LED:** use `netdev` on `phy0-ap0`, or the `phy0tx` trigger.

### The status service: `netlink-ledd`

This daemon drives **PON, LOS, WPS and FXS** as status lamps. Its config is
`/etc/config/netlink_ledd`:

```sh
uci show netlink_ledd
uci set netlink_ledd.main.heartbeat_led='none'    # 'none' disables a role
uci commit netlink_ledd && /etc/init.d/netlink-ledd restart
```

| Option | Default | Role |
|---|---|---|
| `enabled` | `1` | Turn the whole service on or off |
| `internet_up_led` | `green:pon` | On while the internet is reachable |
| `internet_down_led` | `red:los` | On while it isn't |
| `new_client_led` | `green:wps` | Blinks when a Wi-Fi client joins |
| `heartbeat_led` | `green:fxs` | Heartbeat |
| `ap_ifname` | `phy0-ap0` | Wi-Fi interface watched for new clients |
| `check_interval` | `30` | Seconds between internet checks |
| `client_blink_seconds` | `4` | How long the WPS LED blinks |

- "Internet up" means the ping checks succeed; PON then switches to a `netdev` trigger on
  the WAN for traffic flicker.
- To hand an LED back to LuCI, disable its role here first. Otherwise both will fight
  over it.

### Light show

```sh
netlink-lightshow          # about 35 s on all panel lamps; Ctrl-C stops it and restores the LEDs
```

The PAIR button starts it too (§15). To build your own animation, set a lamp's trigger to
`none` and write `brightness` in a loop, or use the `pattern` trigger:

```sh
L=/sys/class/leds/green:fxs
echo pattern > $L/trigger
echo "1 200 0 200 1 50 0 550" > $L/pattern      # brightness/ms pairs: blink-blink-pause
```

### Pins to avoid

Never export or drive these GPIOs:
- **GPIO 17** is the UART TX pad, and touching it kills the serial console.
- **GPIO 32** resets the board.

GPIO 16 is untested, so treat it as off-limits too.

---

## 15. Buttons

All three are polled `gpio-keys` (active-low) and use the standard OpenWrt button
scripts in `/etc/rc.button/`.

| Button | Pin | Key | Action |
|---|---|---|---|
| **RST** | GPIO 38 | `KEY_RESTART` | Short press: reboot. Hold **5 s or more**: **factory reset** (wipes your settings) |
| **WIFI** | GPIO 37 | `KEY_RFKILL` | **Double press** (within 1.5 s) toggles Wi-Fi on/off. A single press does nothing, to avoid accidents |
| **PAIR** | GPIO 41 | `KEY_WPS_BUTTON` | Starts the light show (ignored for the first 90 s after boot) |

Watch button events live:

```sh
logread -f | grep -i button
```

**Change an action:** edit `/etc/rc.button/<name>` or add a script to `/etc/rc.wps/`
(PAIR). For example, to make PAIR start WPS pairing instead of the light show, remove
`/etc/rc.wps/50-netlink-lightshow` and install `hostapd-utils`.

Scripts are told `$ACTION` (`pressed`/`released`) and `$SEEN` (seconds held). Changes to
`/etc/rc.button` survive sysupgrade only if you list them in `/etc/sysupgrade.conf`.

### Failsafe mode

If you lock yourself out: power on with the serial console open. When `Press the [f]
key and hit [enter] to enter failsafe mode` appears, press `f` then Enter. You get a root
shell with the overlay unmounted. Run `firstboot && reboot` to reset settings, or
`mount_root` to repair them.

---

## 16. Network layout and firewall

### Default layout (IoT access point)

```
 Home router (internet, your LAN, e.g. 192.168.1.0/24)
        │ cable
   ┌────┴───────────────────────────────── Netlink (OpenWrt) ──────┐
   │ GE socket ── switch VLAN 3 ── eth0.3 ── "wan": DHCP client     │
   │                                                                │
   │ FE socket ── switch VLAN 2 ── eth0.2 ─┐                         │
   │                                        ├─ br-iot 10.67.67.1/24 │
   │ Wi-Fi "Netlink-IoT" ── phy0-ap0 ───────┘   DHCP .100–.249      │
   └─────────────────────────────────────────────────────────────────┘
```

- The switch keeps the two sockets apart in hardware. Each socket gets its own
  VLAN, `2 + port number`, which the driver exposes as `eth0.2` (FE) and `eth0.3` (GE).
  This is the `rtl9602c_eth.port_vlans=1` mode, set in the device tree.
- **Wi-Fi isn't VLAN-tagged.** It's **bridged** with `eth0.2`, so Wi-Fi and FE devices
  share one isolated network.
- The radio supports **one SSID only**, so there's no guest network or second SSID.

### Firewall policy (generator default)

| From → To | Allowed |
|---|---|
| IoT → internet | **No** (no forwarding at all) |
| IoT → home LAN | **No** |
| IoT → router | DHCP, DNS and NTP only (no SSH or LuCI) |
| Home LAN → IoT | Only from `iot_controller_ip` (for example Home Assistant); default: any home-LAN host |
| Home LAN → router | SSH (key-only), LuCI over HTTPS, ping; from private addresses only |

IPv6 forwarding from IoT is blocked too.

### Reaching IoT devices from your LAN

Your home LAN doesn't know where `10.67.67.0/24` is. Add a **route**
`10.67.67.0/24 via <netlink-wan-ip>`:
- **on the controller host only** (recommended), for example
  `ip route add 10.67.67.0/24 via <netlink-wan-ip>` made persistent in its network
  config;
- or on the home router (its "static routes" page), which makes it visible to the whole
  LAN.

Give IoT devices fixed addresses (LuCI → Network → DHCP and DNS → Static Leases) and add
them in your controller **by IP**. mDNS/SSDP discovery doesn't cross networks. Reserve a
fixed address for the Netlink's WAN on the home router too.

**Devices that must push to the controller** (MQTT on 1883 for Tasmota or Zigbee2MQTT,
Shelly push): add a narrow rule, `iot` → controller IP, that port only.

### Common variations

**Give IoT devices internet:** add a forwarding from `iot` to `wan` in LuCI → Network →
Firewall, or:

```sh
uci add firewall forwarding
uci set firewall.@forwarding[-1].src='iot'
uci set firewall.@forwarding[-1].dest='wan'
uci commit firewall && /etc/init.d/firewall restart
```

Keep a reject rule from `iot` to the private ranges if the home LAN must stay
unreachable.

**Plain Wi-Fi extender / dumb AP:** move `phy0-ap0` into a bridge with `eth0.3` on your
home LAN subnet, set it to a static or DHCP address on that LAN, and disable the router's
DHCP server. Changing the bridge or firewall without a console can lock you out, so keep
serial attached while you do it.

**Client isolation** (IoT devices can't talk to each other):

```sh
uci set wireless.default_radio0.isolate='1' && uci commit wireless && wifi
```

**Hardware NAT** is off (`hw_nat=0`) because isolation depends on software forwarding.
Throughput is fine for IoT traffic, not for gigabit routing.

### Home router caveat

IoT devices can still reach **your home router's public IP**. Any port forward, DMZ host
or UPnP mapping there is reachable from IoT ("hairpin"). Keep DMZ and UPnP off and avoid
unnecessary port forwards.

---

## 17. Recovery and going back to stock

**OpenWrt still boots but you're locked out:** use failsafe (§15), or RST held ≥ 5 s
(factory reset).

**OpenWrt doesn't boot:** U-Boot is never touched, so you can always:
1. Catch U-Boot (§5) and RAM-boot the read-only image (§9.3).
2. Re-flash `k0`/`r0` from U-Boot (§10), or
3. from the RAM image: copy over a sysupgrade file and run `sysupgrade -n`. Note that the
   *read-only* RAM image **cannot** write flash; use U-Boot for that.

### Restore the stock firmware

Put your backed-up **`4-k0.bin`** and **`5-r0.bin`** in the TFTP folder. At `9602C#`:

```
setenv ipaddr 192.168.50.1
setenv serverip 192.168.50.2
tftpboot 0x82000000 4-k0.bin
crc32 ${fileaddr} ${filesize}
sf erase ${fl_kernel1} +${fl_kernel1_sz}
sf write ${fileaddr} ${fl_kernel1} ${filesize}
crc32 b4100000 400000
tftpboot 0x82000000 5-r0.bin
crc32 ${fileaddr} ${filesize}
sf erase ${fl_rootfs1} +${fl_rootfs1_sz}
sf write ${fileaddr} ${fl_rootfs1} ${filesize}
crc32 b4500000 800000
boot
```

The flash CRCs must equal your backup's `crc32 4-k0.bin` / `5-r0.bin` from §6.4.

You never wrote `boot`, `env` or `config`, so nothing else needs restoring.

> Stock firmware mounts the `config` partition and may tidy it (JFFS2 garbage collection)
> when it runs. That's normal: your data is still intact, and your full backup is the
> reference copy anyway.

---

## 18. Troubleshooting and known issues

| Symptom | Cause / fix |
|---|---|
| Garbage or nothing on serial | Wrong ground or baud rate (115200 8N1); TX/RX swapped. For an ESP32 board, `EN` isn't held to GND |
| Commands half-arrive or are mangled | The serial link is lossy (§4.5): type slowly, confirm the echo, use SSH |
| `tftpboot` prints `T T T…` | No link or wrong addresses; PC firewall; NetworkManager removed the PC's static IP; TFTP server bound to another address |
| Stuck at U-Boot after `bootm` | You used the flash kernel file for a RAM boot, or vice versa. RAM boot needs the `initramfs` image |
| WAN gets no address | The cable is in the **FE** socket. WAN is **GE** |
| `/overlay` not JFFS2, settings don't stick | The flash image was built without `MTD_LUNA_NOR_WRITE` (§8.5: `make target/linux/clean`) |
| Both socket LEDs light with one cable | The LED uses `netdev` on `eth0`; switch to `swport0-link` / `swport1-link` |
| A hand-edited script vanished after upgrade | Expected: only config is kept (§12) |
| Official packages crash or misbehave | Wrong CPU target (§2, §13) |
| Wi-Fi clients stall at DHCP | Keep `option multicast_to_unicast '1'` on the AP (the generator sets it) |
| Wi-Fi pauses about 5 s under heavy load | Known **`TXDMA error 0x6000`** in the RTL8192FE driver. A patch restarts the stuck queue in about 3–5 s, without disconnecting clients. Root cause unknown |

**Known limits:**
- One SSID.
- No GPON or FXS.
- Hardware NAT off in the isolated layout.
- Modest Wi-Fi throughput (about 30 Mbit/s average, 80 Mbit/s peak in tests).
- Failsafe gives a passwordless root shell (physical serial access only).

---

## 19. Reference

### Useful commands on the router

```sh
cat /tmp/sysinfo/model                           # board model
cat /proc/cpuinfo | head                         # "Realtek Taroko (RLX)"
cat /proc/mtd                                    # partitions
dmesg | grep -iE "luna|rtl9602|rtl8192"          # driver messages
logread -e netlink                               # front-panel services
ip -br link; ip -br addr                         # interfaces
iw dev phy0-ap0 station dump                     # Wi-Fi clients
cat /sys/module/rtl9602c_eth/parameters/*        # switch driver settings
fw4 print | less                                 # active firewall
```

### Kernel parameters (set in the device tree; overridable in U-Boot `bootargs`)

| Parameter | Default | Meaning |
|---|---|---|
| `rtl9602c_eth.port_vlans` | `1` | Each socket its own VLAN (`eth0.2` FE, `eth0.3` GE) |
| `rtl9602c_eth.hw_nat` | `0` | Hardware NAT (off for isolation) |
| `rtl9602c_eth.port_led_activity` | `Y` | Socket LEDs flicker with traffic (writable at runtime) |

### Files added by the Netlink overlay

| Path | Role |
|---|---|
| `/etc/init.d/netlink-wifi-cal` | Loads the unit's Wi-Fi calibration before networking |
| `/usr/bin/netlink-ledd`, `/etc/init.d/netlink-ledd`, `/etc/config/netlink_ledd` | Status LEDs |
| `/usr/bin/netlink-lightshow` | Light show |
| `/etc/rc.button/rfkill` | WIFI button (double press) |
| `/etc/rc.wps/50-netlink-lightshow` | PAIR button |

### Source layout (fork, branch `netlink`)

| Path | Contents |
|---|---|
| `target/linux/realtek-luna/…/rtl9602c_netlink_hg323rgw.dts` | Board device tree: partitions, LEDs, buttons, kernel arguments |
| `target/linux/realtek-luna/image/taroko.mk` | Image recipe (`netlink_hg323rgw`) |
| `…/drivers/net/ethernet/realtek/rtl9602c_eth.c` | Switch/Ethernet driver: per-socket VLANs, link-LED triggers, traffic flicker |
| `…/rtlwifi/rtl8192fe/` | Wi-Fi driver, with the fast TX-stall recovery |
| `netlink/` | Build seed, overlay generator, `unit.example.json`, front-panel tools, README |

### U-Boot quick card

```
stop autoboot     any key during "Hit any key to stop autoboot"
show offsets      printenv fl_kernel1 fl_kernel1_sz fl_rootfs1 fl_rootfs1_sz
RAM boot          setenv ipaddr …; setenv serverip …; setenv bootargs console=ttyS0,115200
                  tftpboot 0x82000000 <ram-image>; bootm 0x82000000
write k0          tftpboot 0x82000000 <kernel>; sf erase ${fl_kernel1} +${fl_kernel1_sz}
                  sf write ${fileaddr} ${fl_kernel1} ${filesize}; crc32 b4100000 ${filesize}
write r0          tftpboot 0x82000000 <rootfs>; sf erase ${fl_rootfs1} +${fl_rootfs1_sz}
                  sf write ${fileaddr} ${fl_rootfs1} ${filesize}; crc32 b4500000 ${filesize}
stock shell       setenv bootargs_base 'console=ttyS0,115200 init=/bin/sh'; boot
NEVER             saveenv · upb/upk/upr/upt/yu · typed offsets
```
