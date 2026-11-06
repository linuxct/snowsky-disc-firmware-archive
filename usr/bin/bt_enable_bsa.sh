#!/bin/sh
tty_dev=/dev/ttyS0
#firmware_path=/lib/firmware/bt_bcm/BCM4343A1_001.002.009.1026.1055.hcd
firmware_path=/usr/data/ap6256/bt_bcm/BCM4345C5_003.006.006.1055.1114.hcd

killall bsa_server

rfkill block bluetooth
rfkill unblock bluetooth

sleep 1
mkdir -p /run/blue_bsa
bsa_server -all=0 -d $tty_dev -p $firmware_path -u /run/blue_bsa/ -k /run/blue_bsa/ble_local_keys -b /tmp/hci_snoop.log > /tmp/bsa_server.log &

