#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

IMAGES=../dist
OUT_ROOTFS_TAR="$IMAGES"/alpine-rootfs.tar
OUT_ROOTFS_FLAT="$IMAGES"/alpine-rootfs-flat
OUT_FSJSON="$IMAGES"/alpine-fs.json
CONTAINER_NAME=alpine-v86
IMAGE_NAME=i386/alpine-v86

mkdir -p "$IMAGES"

# Use buildx with cache if DOCKER_CACHE_DIR is set (e.g., in CI)
if [ -n "${DOCKER_CACHE_DIR:-}" ]; then
    docker buildx build . \
        --platform linux/386 \
        --cache-from type=local,src="$DOCKER_CACHE_DIR" \
        --cache-to type=local,dest="${DOCKER_CACHE_DIR}-new" \
        --tag "$IMAGE_NAME" \
        --load
else
    docker build . --platform linux/386 --rm --tag "$IMAGE_NAME"
fi

docker rm "$CONTAINER_NAME" || true
docker create --platform linux/386 -t -i --name "$CONTAINER_NAME" "$IMAGE_NAME"

docker export "$CONTAINER_NAME" -o "$OUT_ROOTFS_TAR"

# https://github.com/iximiuz/docker-to-linux/issues/19#issuecomment-1242809707
tar -f "$OUT_ROOTFS_TAR" --delete ".dockerenv" || true

./fs2json.py --zstd --out "$OUT_FSJSON" "$OUT_ROOTFS_TAR"

# Note: Not deleting old files here
mkdir -p "$OUT_ROOTFS_FLAT"
./copy-to-sha256.py --zstd -j "$(nproc)" "$OUT_ROOTFS_TAR" "$OUT_ROOTFS_FLAT"

echo "$OUT_ROOTFS_TAR", "$OUT_ROOTFS_FLAT" and "$OUT_FSJSON" created.
