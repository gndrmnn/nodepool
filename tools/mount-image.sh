IMAGE=$1

rm -rf /tmp/newimage
mkdir -p /tmp/newimage

qemu-nbd -c /dev/nbd1 $1
mount /dev/nbd1 /tmp/newimage
