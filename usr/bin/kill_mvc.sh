killall -9 fiio_init.sh 
killall -9 mq_player 
killall -9 mq_ui
killall -9 udhcpc
killall -9 wpa_supplicant

#AIRPLAY相关服务
killall -9 avahi-publish-service
/etc/init.d/S50avahi-daemon stop
/etc/init.d/S50avahi-daemon start

