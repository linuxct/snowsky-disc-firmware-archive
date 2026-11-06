#!/bin/sh

killall -9 udhcpc
killall -9 wpa_supplicant
ifconfig eth1 0.0.0.0
ifconfig eth1 down