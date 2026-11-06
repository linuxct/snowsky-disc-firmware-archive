#!/bin/sh

VID=0x0525
PID=0xa4a5

UDC=`ls /sys/class/udc/` # UDC=13500000.otg

prg_name=$0
status=$1
mount_paths=$2

add_img() {
	local dev_name=$1
	local mount_path=$2

	if [ -f $mount_path ] || [ -b $mount_path ]; then
		mkdir -p functions/mass_storage.msg/$dev_name
		echo 0 > functions/mass_storage.msg/$dev_name/ro
		echo 1 > functions/mass_storage.msg/$dev_name/removable
		echo 0 > functions/mass_storage.msg/$dev_name/nofua
		echo 0 > functions/mass_storage.msg/$dev_name/cdrom
		echo $mount_path > functions/mass_storage.msg/$dev_name/file
	else
		echo "mount_path is null"
	fi
}

# 配置mass storage设备
storage_start() {
	echo "Creating the USB gadget"
	mount -t configfs none /sys/kernel/config

	echo "Creating gadget directory storage_demo"
	mkdir /sys/kernel/config/usb_gadget/storage_demo
	cd /sys/kernel/config/usb_gadget/storage_demo

	# 配置设备描述符
	echo "Setting Device Descriptor..."
	echo "0x00" > bDeviceClass
	echo "0x0200" > bcdUSB
	echo "0x0100" > bcdDevice
	echo $VID > idVendor
	echo $PID > idProduct

	# 配置字符串描述符
	echo "Setting English strings..."
	mkdir strings/0x409
	echo "ingenic" > strings/0x409/manufacturer
	echo "composite-storage" > strings/0x409/product
	echo "ingenic-storage" > strings/0x409/serialnumber

	# 配置配置描述符
	echo "Creating Config..."
	mkdir configs/c.1/
	echo "120" > configs/c.1/MaxPower
	echo "0x80" > configs/c.1/bmAttributes
	mkdir configs/c.1/strings/0x409/
	echo "storage" > configs/c.1/strings/0x409/configuration

	#初始化挂载目录
	mkdir functions/mass_storage.msg/

	num=0
	path_array=${mount_paths//,/ }
	for path in $path_array
	do
		add_img lun.$num $path
		let num+=1
	done

	ln -s functions/mass_storage.msg configs/c.1

	echo "Binding USB Device Controller"
	echo $UDC > UDC
	echo "ok"
}

# 卸载mass storage设备
storage_stop() {
	echo "stopping the USB gadget"

	if [ ! -d /sys/kernel/config/usb_gadget/storage_demo ]; then
		echo "Error: usb configfs storage_demo uninitialized"
		exit 1
	fi
	cd /sys/kernel/config/usb_gadget/storage_demo

	echo "Unbinding USB Device controller..."
	echo "" > UDC
	echo "ok"

	echo "Deleting mass storage gadget functionality : "
	rm configs/c.1/mass_storage.msg

	rmdir functions/mass_storage.msg/lun.*/
	rmdir functions/mass_storage.msg
	echo "ok"

	echo "Cleaning up configuration..."
	rmdir configs/c.1/strings/0x409
	rmdir configs/c.1
	echo "ok"

	echo "cleaning English string..."
	rmdir strings/0x409
	echo "ok"

	echo "Removing gadget directory..."
	cd -
	rmdir /sys/kernel/config/usb_gadget/storage_demo/
	echo "ok"

	umount /sys/kernel/config
}

case "$status" in
  start)
	if [ "$#" != "2" ]; then
		echo "Usage1: $prg_name start <mount_path,mount_path.....>"
		exit 1
	fi

	if [ -d /sys/kernel/config/usb_gadget ]; then
		echo "Error: usb configfs already mounted"
		exit 1
	fi

	# 配置storage设备函数
	storage_start
	;;

  stop)
	if [ "$#" != "1" ]; then
		echo "Usage: $prg_name stop"
		exit 1
	fi

	if [ ! -d /sys/kernel/config/usb_gadget/storage_demo/ ]; then
		echo "Error: usb configfs storage_demo uninitialized"
		exit 1
	fi

	# 卸载mass storage设备
	storage_stop
	;;
	*)
	echo "Usage1: $prg_name start"
	echo "Usage2: $prg_name stop"
	exit 1
esac
