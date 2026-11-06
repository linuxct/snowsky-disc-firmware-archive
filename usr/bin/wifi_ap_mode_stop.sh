#!/bin/sh

killall udhcpd
killall hostapd

# ifconfig ${env_wifi_hostap_interface} 0.0.0.0
# ifconfig ${env_wifi_hostap_interface} down

ifconfig wlan0 0.0.0.0
ifconfig wlan0 down

sleep 1

rfkill block wifi

/etc/init.d/S80dnsmasq start
