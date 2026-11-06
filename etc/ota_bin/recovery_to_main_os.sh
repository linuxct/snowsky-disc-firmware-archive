#!/bin/sh

#mtd5是根据分区来划分，mtd5是recovery系统的kernel2的分区
flash_erase /dev/mtd5 0 1

#ota:kernel2中的kernel2是分区名，烧录时自定义，mtd5同上
printf "%-256s" "ota:kernel" | nandwrite -s 0 -p /dev/mtd5 -
reboot -f