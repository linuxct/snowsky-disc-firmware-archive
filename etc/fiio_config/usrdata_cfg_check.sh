#!/bin/sh
echo "check fiio usrdata cfg!" > /dev/console

if ! pgrep -x "/usr/sbin/sshd" > /dev/null; then
	echo "Process '/usr/sbin/sshd' is not running. Restarting..."
	mkdir -p /usr/data/ssh
	rm -rf /usr/data/ssh/*
	cp -f /etc/fiio_config/ssh_cfg/* /usr/data/ssh/
	/usr/bin/ssh-keygen -A
	
	# 重新启动进程
	/usr/sbin/sshd &
fi

if [ ! -f "/usr/data/localtime" ]; then
    echo "File /usr/data/localtime does not exist, copy from fiio_config"
	cp -f /etc/fiio_config/localtime_cfg/Shanghai	/usr/data/localtime
fi

if [ ! -f "/usr/data/samba4" ]; then
    echo "File /usr/data/samba4 does not exist, copy from fiio_config"
	mkdir -p /usr/data/samba4
	rm -rf /usr/data/samba4/*
	cp -f /etc/fiio_config/samba4_cfg/* /usr/data/samba4/
fi
