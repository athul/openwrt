"""Netlink HG323RGW overlay generator: writes <openwrt-dir>/files/ for an IoT
access-point image (see netlink/README.md).

usage: prepare-overlay.py <openwrt-dir> <admin-ssh-pubkey-file> [unit.json]

Unit-specific values (factory MACs, Wi-Fi calibration, Wi-Fi country, an
optional IoT-side LuCI host) come from unit.json (default: netlink/unit.json,
git-ignored; template: netlink/unit.example.json). Missing values are skipped:
no MAC override, no calibration service, country '00' (world).
"""
from pathlib import Path
import json
import re
import sys
r=Path(sys.argv[1]);o=r/'files';(o/'etc/config').mkdir(parents=True,exist_ok=True)
(o/'etc/dropbear').mkdir(parents=True,exist_ok=True)
# Replaces the fork's inherited keys. argv[2] (required): an admin public key
# file; only its type and key fields are kept (the user@host comment is
# dropped). Required because dropbear below is key-only: an empty file would
# leave serial as the only way in.
if len(sys.argv)<3: sys.exit('usage: prepare-overlay.py <openwrt-dir> <admin-ssh-pubkey-file> [unit.json]')
f=Path(sys.argv[2]).read_text().split()
if len(f)<2 or f[0] not in ('ssh-ed25519','ssh-rsa'): sys.exit('need an ssh-ed25519 or ssh-rsa public key: '+sys.argv[2])
admin_key=f[0]+' '+f[1]+'\n'
here=Path(__file__).resolve().parent
unit_path=Path(sys.argv[3]) if len(sys.argv)>3 else here/'unit.json'
unit=json.loads(unit_path.read_text()) if unit_path.is_file() else {}
if not unit: print('note: no',unit_path,'- building without unit MACs/calibration')
MAC=re.compile(r'^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$')
def umac(k):
 v=unit.get(k)
 if v is None: return None
 if not MAC.match(v): sys.exit(f'unit.json {k}: not a MAC: {v!r}')
 return v.lower()
lan_mac,wifi_mac,iot_luci_mac=umac('lan_mac'),umac('wifi_mac'),umac('luci_iot_mac')
country=unit.get('country','00')
if not re.match(r'^[A-Z0-9]{2}$',country): sys.exit(f'unit.json country: {country!r}')
(o/'etc/dropbear/authorized_keys').write_text(admin_key)
# Custom files are copied after target defaults. Keep this initramfs isolated
# from lab-specific identity, GPON recovery and persistent bank commit services.
for tree in ['target/linux/realtek-luna/base-files/etc/uci-defaults','target/linux/gpon-common/base-files/etc/uci-defaults']:
 for src in (r/tree).glob('*'):
  if src.is_file():
   dst=o/'etc/uci-defaults'/src.name;dst.parent.mkdir(parents=True,exist_ok=True)
   dst.write_text('#!/bin/sh\n# Netlink RAM bring-up: target laboratory defaults intentionally skipped.\nexit 0\n');dst.chmod(0o755)
for name in ['gpon-wan-recover','onu-bank-commit','onu_crashlog','telnet']:
 dst=o/'etc/init.d'/name;dst.parent.mkdir(parents=True,exist_ok=True)
 dst.write_text('#!/bin/sh /etc/rc.common\n# No service for Netlink RAM bring-up.\nstart() { :; }\n');dst.chmod(0o755)
# This unit's factory Wi-Fi calibration (HW_WLAN0_* from its own config
# partition, read with rtk_factory). rtl8192fe is built in and re-applies
# unit_* at every radio start, so loading them before netifd (START=20) is
# enough. Power arrays are 14 MIB hex bytes and need a 0x prefix (base-0
# parse); thermal/xcap/reg_domain are decimal MIB integers.
cal_in=unit.get('wifi_cal')
if cal_in:
 HEX14=re.compile(r'^0x[0-9a-fA-F]{2}(,0x[0-9a-fA-F]{2}){13}$')
 for k in ('cck_a','cck_b','ht40_a','ht40_b'):
  if not HEX14.match(str(cal_in.get(k,''))): sys.exit(f'unit.json wifi_cal.{k}: need 14 comma-separated 0xNN bytes')
 for k in ('thermal','xcap','reg_domain'):
  if not isinstance(cal_in.get(k),int): sys.exit(f'unit.json wifi_cal.{k}: need an integer')
 lines=''.join(f'\techo {cal_in[k]} > "$P/unit_{k}" &&\n' for k in ('cck_a','cck_b','ht40_a','ht40_b','thermal','xcap','reg_domain'))
 cal=o/'etc/init.d/netlink-wifi-cal';cal.parent.mkdir(parents=True,exist_ok=True)
 cal.write_text("""#!/bin/sh /etc/rc.common
# Hand this unit's factory Wi-Fi calibration to rtl8192fe before the radio starts.
START=19

start() {
	P=/sys/module/rtl8192fe/parameters
	if [ ! -d "$P" ]; then
		logger -t netlink-wifi-cal "rtl8192fe parameters missing: radio stays uncalibrated"
		return 1
	fi
"""+lines+"""	logger -t netlink-wifi-cal "unit calibration loaded" ||
	logger -t netlink-wifi-cal "failed to load unit calibration"
}
""");cal.chmod(0o755)
# Dedicated IoT access point behind the home router. REQUIRES the boot argument
# rtl9602c_eth.port_vlans=1: the switch then gives each copper socket its own
# VLAN (VID 2 + port), untagged on the socket and tagged to the CPU, so
# eth0.3 = port 1 socket (gigabit per the register map) = WAN uplink (DHCP from
# the home router) and eth0.2 = port 0 socket (100 Mbit) = wired IoT, bridged
# with the single SSID into the isolated 'iot' network 10.67.67.0/24, NATed
# out. Also needs the rtl9602c-port-vlans-isolation driver patch. Without the argument bare
# eth0 carries untagged LAN traffic and neither VLAN device receives anything.
(o/'etc/config/network').write_text("""config interface 'loopback'
 option device 'lo'
 option proto 'static'
 option ipaddr '127.0.0.1'
 option netmask '255.0.0.0'

config interface 'wan'
 option device 'eth0.3'
 option proto 'dhcp'

config device
 option name 'br-iot'
 option type 'bridge'
 option bridge_empty '1'
 list ports 'eth0.2'

config interface 'iot'
 option device 'br-iot'
 option proto 'static'
 option ipaddr '10.67.67.1'
 option netmask '255.255.255.0'
"""+(f"""
# This unit's factory LAN MAC (unit.json lan_mac); without it the NIC uses the
# MAC engine's shared silicon default.
config device
 option name 'eth0'
 option macaddr '{lan_mac}'
""" if lan_mac else ''))
(o/'etc/config/dhcp').write_text("""config dnsmasq
 option domainneeded '1'
 option boguspriv '1'
 option localise_queries '1'
 option rebind_protection '1'
 option rebind_localhost '1'
 option local '/iot/'
 option domain 'iot'
 option expandhosts '1'
 option cachesize '1000'
 option authoritative '1'
 option readethers '1'
 option leasefile '/tmp/dhcp.leases'
 option resolvfile '/tmp/resolv.conf.d/resolv.conf.auto'
 option nonwildcard '1'
 option localservice '1'
 list notinterface 'wan'

config dhcp 'iot'
 option interface 'iot'
 option start '100'
 option limit '150'
 option leasetime '12h'

config dhcp 'wan'
 option interface 'wan'
 option ignore '1'
""")
# IoT may reach the internet only: private destinations (the home LAN among
# them) are rejected before the iot->wan forwarding applies; towards the router
# itself only DHCP and DNS. SSH is accepted from private source addresses on the
# WAN side (home LAN, incl. a tailnet jump host there) and is key-only below.
(o/'etc/config/firewall').write_text("""config defaults
 option syn_flood '1'
 option input 'REJECT'
 option output 'ACCEPT'
 option forward 'REJECT'

config zone
 option name 'wan'
 list network 'wan'
 option input 'REJECT'
 option output 'ACCEPT'
 option forward 'REJECT'
 option masq '1'
 option mtu_fix '1'

config zone
 option name 'iot'
 list network 'iot'
 option input 'REJECT'
 option output 'ACCEPT'
 option forward 'REJECT'

config forwarding
 option src 'iot'
 option dest 'wan'

config rule
 option name 'Block-IoT-to-private'
 option src 'iot'
 option dest 'wan'
 option proto 'all'
 list dest_ip '10.0.0.0/8'
 list dest_ip '172.16.0.0/12'
 list dest_ip '192.168.0.0/16'
 list dest_ip '169.254.0.0/16'
 list dest_ip '100.64.0.0/10'
 option target 'REJECT'

config rule
 option name 'Block-IoT-IPv6-forward'
 option src 'iot'
 option dest '*'
 option proto 'all'
 option family 'ipv6'
 option target 'REJECT'

config rule
 option name 'Allow-IoT-DHCP'
 option src 'iot'
 option proto 'udp'
 option dest_port '67'
 option family 'ipv4'
 option target 'ACCEPT'

config rule
 option name 'Allow-IoT-DNS'
 option src 'iot'
 option proto 'tcp udp'
 option dest_port '53'
 option target 'ACCEPT'

config rule
 option name 'Allow-DHCP-Renew'
 option src 'wan'
 option proto 'udp'
 option dest_port '68'
 option family 'ipv4'
 option target 'ACCEPT'

config rule
 option name 'Allow-Ping-WAN'
 option src 'wan'
 option proto 'icmp'
 option icmp_type 'echo-request'
 option family 'ipv4'
 option target 'ACCEPT'

config rule
 option name 'Allow-SSH-from-home'
 option src 'wan'
 option proto 'tcp'
 option dest_port '22'
 list src_ip '10.0.0.0/8'
 list src_ip '172.16.0.0/12'
 list src_ip '192.168.0.0/16'
 option family 'ipv4'
 option target 'ACCEPT'

config rule
 option name 'Allow-LuCI-from-home'
 option src 'wan'
 option proto 'tcp'
 list dest_port '80'
 list dest_port '443'
 list src_ip '10.0.0.0/8'
 list src_ip '172.16.0.0/12'
 list src_ip '192.168.0.0/16'
 option family 'ipv4'
 option target 'ACCEPT'

"""+(f"""
# Optional (unit.json luci_iot_mac): LuCI from ONE host on the IoT side,
# matched by MAC, e.g. a build host wired to the IoT socket before the WAN is
# cabled. Normally LuCI is reached from the home LAN via Allow-LuCI-from-home.
config rule
 option name 'Allow-LuCI-IoT-host'
 option src 'iot'
 option proto 'tcp'
 list dest_port '80'
 list dest_port '443'
 option src_mac '{iot_luci_mac}'
 option family 'ipv4'
 option target 'ACCEPT'
""" if iot_luci_mac else ''))
# Root has no password in this image, so SSH must be key-only.
(o/'etc/config/dropbear').write_text("""config dropbear
 option PasswordAuth 'off'
 option RootPasswordAuth 'off'
 option Port '22'
""")
# Radio on at every boot, but no key in the image: with psk2 and an empty key
# hostapd loads an empty PSK file, so nobody can join until a key is set once:
#   uci set wireless.default_radio0.key='...'; uci commit wireless; wifi
# (/etc/config/wireless is kept across sysupgrade, so the key stays.)
# BSSID: unit.json wifi_mac (on the reference unit the label MAC + 1; the stored
# WLAN_MAC_ADDR has a generic Realtek prefix).
(o/'etc/config/wireless').write_text(f"""config wifi-device 'radio0'
 option type 'mac80211'
 option phy 'phy0'
 option band '2g'
 option channel '1'
 option htmode 'HT20'
 option country '{country}'
 option disabled '0'

config wifi-iface 'default_radio0'
 option device 'radio0'
 option network 'iot'
 option mode 'ap'
 option ssid 'Netlink-IoT'
 option encryption 'psk2'
 option multicast_to_unicast '1'
"""+(f" option macaddr '{wifi_mac}'\n" if wifi_mac else ''))
# Front-panel extras kept beside this generator: the light show, the status
# LED service and the button handlers (sources -> image path, mode).
for src,dst,mode in [
 ('netlink-lightshow','usr/bin/netlink-lightshow',0o755),
 ('netlink-ledd','usr/bin/netlink-ledd',0o755),
 ('netlink-ledd.init','etc/init.d/netlink-ledd',0o755),
 ('rc.button-rfkill','etc/rc.button/rfkill',0o755),	# WIFI: double press toggles radios
 ('rc.wps-lightshow','etc/rc.wps/50-netlink-lightshow',0o755),	# PAIR: light show
]:
 s=here/src
 if not s.is_file(): sys.exit('missing '+str(s))
 d=o/dst;d.parent.mkdir(parents=True,exist_ok=True)
 d.write_text(s.read_text());d.chmod(mode)
# netlink-ledd settings. Set an LED name to 'none' to disable that role; edit,
# then `/etc/init.d/netlink-ledd reload`.
(o/'etc/config/netlink_ledd').write_text("""config ledd 'main'
 option enabled '1'
 option internet_up_led 'green:pon'
 option internet_down_led 'red:los'
 option new_client_led 'green:wps'
 option heartbeat_led 'green:fxs'
 option ap_ifname 'phy0-ap0'
 option check_interval '30'
 option client_blink_seconds '4'
 # At most four hosts are tried per check, each for at most two seconds.
 list check_host '1.1.1.1'
 list check_host '8.8.8.8'
""")
# netlink-ledd is started by its init script; enable it at first boot.
ud=o/'etc/uci-defaults/90-netlink-ledd';ud.parent.mkdir(parents=True,exist_ok=True)
ud.write_text('#!/bin/sh\n/etc/init.d/netlink-ledd enable\nexit 0\n');ud.chmod(0o755)
print('Prepared overlay:',o)
