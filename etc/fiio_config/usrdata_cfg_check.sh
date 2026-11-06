#!/bin/sh
echo "check fiio usrdata cfg!" > /dev/console

if ! pgrep -x "/usr/sbin/sshd" > /dev/null; then
	echo "Process '/usr/sbin/sshd' is not running. Restarting..."
	mkdir -p /usr/data/ssh
	rm /usr/data/ssh/* -rf
	cp /etc/fiio_config/ssh_cfg/* /usr/data/ssh/ -f
	/usr/bin/ssh-keygen -A
	
	# 重新启动进程
	/usr/sbin/sshd &
fi

if [ ! -f "/usr/data/localtime" ]; then
    echo "File /usr/data/localtime does not exist, copy from fiio_config"
	cp /etc/fiio_config/localtime_cfg/Shanghai	/usr/data/localtime -f
fi
