#!/bin/sh

killall dnsmasq
killall udhcpd
killall hostapd

rfkill unblock wifi

sleep 1

#ifconfig ${env_wifi_hostap_interface} up
ifconfig wlan0 up

if [ ! -e /usr/data/udhcpd.leases ]
then
    mkdir -p /usr/data
    touch /usr/data/udhcpd.leases
    chmod +x /usr/data/udhcpd.leases
fi

hostapd -B /etc/hostapd.conf  &

#ifconfig ${env_wifi_hostap_interface} 192.168.1.1 netmask 255.255.255.0
ifconfig wlan0 192.168.237.1 netmask 255.255.255.0

udhcpd -f /etc/udhcpd.conf &
