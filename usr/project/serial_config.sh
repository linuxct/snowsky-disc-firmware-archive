#!/bin/sh

VID=0x0525
PID=0xa4a7

UDC=`ls /sys/class/udc/` # UDC=13500000.otg

prg_name=$0
status=$1


# 配置serial设备
serial_start() {
	echo "Creating the USB gadget..."
	mount -t configfs none /sys/kernel/config

	mkdir /sys/kernel/config/usb_gadget/serial_demo
	cd /sys/kernel/config/usb_gadget/serial_demo

	# 配置设备描述符
	echo "Setting Device Descriptor..."
	echo "0x02" > bDeviceClass
	echo "0x00" > bDeviceSubClass
	echo "0x00" > bDeviceProtocol
	echo "0x0200" > bcdUSB
	echo "0x2400" > bcdDevice
	echo $VID > idVendor
	echo $PID > idProduct

	#配置字符串描述符
	echo "Setting English strings..."
	mkdir strings/0x409
	echo "INGENIC" > strings/0x409/manufacturer
	echo "Gadget Serial v2.4" > strings/0x409/product
	echo "ingenic-serial" > strings/0x409/serialnumber

	#配置配置描述符
	echo "Creating Config..."
	mkdir configs/c.1
	echo "120" > configs/c.1/MaxPower
	echo "0x80" > configs/c.1/bmAttributes
	mkdir configs/c.1/strings/0x409
	echo "INGENIC" > configs/c.1/strings/0x409/configuration

	#配置功能描述符
	echo "Creating functions..."
	mkdir functions/acm.0

	ln -s functions/acm.0 configs/c.1

	echo $UDC > UDC
}

# 卸载serial设备
serial_stop() {
	echo "stopping the USB gadget"

	cd /sys/kernel/config/usb_gadget/serial_demo

	echo "Unbinding USB Device controller..."
	echo "" > UDC

	echo "Deleting SERIAL gadget functionality : acm.0"
	rm configs/c.1/acm.0
	rmdir functions/acm.0

	echo "Cleaning up configuration..."
	rmdir configs/c.1/strings/0x409
	rmdir configs/c.1

	echo "cleaning English string..."
	rmdir strings/0x409

	echo "Removing gadget directory..."
	cd -
	rmdir /sys/kernel/config/usb_gadget/serial_demo/

	umount /sys/kernel/config
}

case "$status" in
  start)
	if [ "$#" != "1" ]; then
		echo "Usage1: $prg_name <start|stop>"
		exit 1
	fi

	if [ -d /sys/kernel/config/usb_gadget ]; then
		echo "Error: usb configfs already mounted"
		exit 1
	fi

	# 配置serial设备函数
	serial_start
	;;

  stop)
	if [ "$#" != "1" ]; then
		echo "Usage: $prg_name <start|stop>"
		exit 1
	fi

	if [ ! -d /sys/kernel/config/usb_gadget/serial_demo/ ]; then
		echo "Error: usb configfs serial_demo uninitialized"
		exit 1
	fi

	# 卸载serial设备
	serial_stop
	;;
	*)
	echo "Usage: $prg_name <start|stop>"
	exit 1
esac
