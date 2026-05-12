FROM docker.io/i386/alpine:3.21.0

ARG APK_MIN="openrc alpine-base agetty alpine-conf linux-virt linux-firmware-none"
ARG APK_EXTRA="python3 build-base rust cargo rustfmt nodejs npm go pkgconf git"

RUN apk add --no-cache $APK_MIN $APK_EXTRA

# Setup autologin
RUN sed -i 's/getty 38400 tty1/agetty --autologin root tty1 linux/' /etc/inittab
RUN echo 'ttyS0::respawn:/sbin/agetty --autologin root -s ttyS0 115200 vt100' >> /etc/inittab
RUN echo "root:" | chpasswd

# https://wiki.alpinelinux.org/wiki/Alpine_Linux_in_a_chroot#Preparing_init_services
RUN for i in devfs dmesg mdev hwdrivers; do rc-update add $i sysinit; done
RUN for i in hwclock modules sysctl hostname syslog bootmisc; do rc-update add $i boot; done
RUN rc-update add killprocs shutdown

# Generate initramfs with 9p modules
RUN mkinitfs -F "base virtio 9p" $(cat /usr/share/kernel/virt/kernel.release)

# Add files
COPY fs/ /
