#!/bin/sh

/etc/fiio_config/usrdata_cfg_check.sh &
/usr/project/switch_usb.sh 0 &  # 开机切换到X2000烧录模式
echo 0 > /proc/sys/kernel/printk    # 关闭内核日志，解决wifi和u盘插入影响到pmw灯闪烁的问题
cp -f /usr/project/config/zlog_player.conf /usr/data/fiio/log/ # 将日志配置每次都拷贝一次(出货固件时，日本配置会再关闭Debug等级)
cp -f /usr/project/config/zlog_ui.conf /usr/data/fiio/log/ # 将日志配置每次都拷贝一次(出货固件时，日本配置会再关闭Debug等级)
rm -f /usr/data/fiio/ota_patch.json
rm -f /usr/data/fiio/ota_upgrade
rm -f /usr/data/fiio/ota_url
rm -f /usr/data/fiio/bt_state

COREDUMP_FLAG="0"
if [ -f "/etc/product_version/version.in" ]; then
    echo "========SYS INFO=========="
    cat /etc/product_version/version.in
    echo "========SYS INFO=========="

    BUILD_TYPE=$(grep "^BUILD_TYPE=" /etc/product_version/version.in | cut -d'=' -f2 | tr -d '\r')
    echo "BUILD_TYPE = $BUILD_TYPE"
    if [ "$BUILD_TYPE" = "debug" ]; then
        COREDUMP_FLAG="1"
        echo 0 > /proc/sys/kernel/randomize_va_space
        echo 7 > /proc/sys/kernel/printk 
    fi
fi

if [ "$COREDUMP_FLAG" == "1" ]; then
    echo "set core dump"
    # 启用 core dump 生成
    ulimit -c unlimited

    # 设置 core dump 文件保存路径
    echo "/usr/data/fiio/log/core.%e.%t" | tee /proc/sys/kernel/core_pattern
fi

# 设置LD_LIBRARY_PATH环境变量
export LD_LIBRARY_PATH="/usr/lib:/usr/lib/pulseaudio/:/usr/data/lib:/usr/data/lib/ffmpeg"

if [ "$COREDUMP_FLAG" == "1" ]; then
PROCESS_MQ_UI="/usr/data/mq_ui"
PROCESS_MQ_PLAYER="/usr/data/mq_player"
else
PROCESS_MQ_UI="mq_ui"
PROCESS_MQ_PLAYER="mq_player"
fi


if [ "$COREDUMP_FLAG" == "1" ]; then
    /usr/data/mq_ui &
    sleep 2
    /usr/data/mq_player &
else
    #启动程序
    mq_ui &
    sleep 2
    mq_player &
fi

if [ "$BUILD_TYPE" = "debug" ]; then
    PID_PLAYER=$(pidof mq_player)
    PID_UI=$(pidof mq_ui)

    if [ -n "$PID_PLAYER" ]; then
        cat /proc/$PID_PLAYER/maps >> /usr/data/fiio/log/mq_player_maps
    else
        echo "mq_player process not found"
    fi

    if [ -n "$PID_UI" ]; then
        cat /proc/$PID_UI/maps >> /usr/data/fiio/log/mq_ui_maps
    else
        echo "mq_player process not found"
    fi
fi

sleep 3
while true; do
    # 检查进程是否在运行
    if ! pgrep -x "$PROCESS_MQ_UI" > /dev/null; then
        echo "Process '$PROCESS_MQ_UI' is not running. Restarting..."
        echo "$(date) - mq_ui Restarting" >> /usr/data/fiio/log/process_failed.txt
        echo " " >> /usr/data/fiio/log/process_failed.txt

        killall -9 mq_player 
        killall -9 mq_ui
        killall -9 udhcpc
        killall -9 wpa_supplicant
        killall -9 curl
        killall -9 wget
        killall -9 bluetoothd
        killall -9 bluealsa
        killall -9 brcm_patchram_plus

        echo 0 > /sys/bus/platform/drivers/pwm-backlight/backlight/backlight/backlight/brightness   # 关闭背光

        if [ "$COREDUMP_FLAG" == "1" ]; then
            exit 0
        fi

        mq_ui &
        sleep 2
        # 重新启动进程
        mq_player &
    fi
    
    # 检查进程是否在运行
    if ! pgrep -x "$PROCESS_MQ_PLAYER" > /dev/null; then
        echo "Process '$PROCESS_MQ_PLAYER' is not running. Restarting..."
        echo "$(date) - mq_player Restarting" >> /usr/data/fiio/log/process_failed.txt
        echo " " >> /usr/data/fiio/log/process_failed.txt

        killall -9 mq_player 
        killall -9 mq_ui
        killall -9 udhcpc
        killall -9 wpa_supplicant
        killall -9 curl
        killall -9 wget
        killall -9 bluetoothd
        killall -9 bluealsa
        killall -9 brcm_patchram_plus

        if [ "$COREDUMP_FLAG" == "1" ]; then
            exit 0
        fi

        mq_ui &
        sleep 2
        # 重新启动进程
        mq_player &
    fi
    # 每隔一段时间检查一次
    sleep 5  # 可以根据需要调整检查的时间间隔
done &
