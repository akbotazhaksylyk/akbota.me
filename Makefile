.PHONY: build run clean help

# Default target
.DEFAULT_GOAL := build

help:
	@echo "Available targets:"
	@echo "  make build  - Build Alpine rootfs and state (default)"
	@echo "  make run    - Start HTTP server on port 8000"
	@echo "  make clean  - Remove build artifacts"

build:
	@echo "Building Alpine rootfs..."
	cd alpine && ./build.sh
	@echo "Building Alpine state..."
	cd alpine && ./build-state.js
	@echo "Compressing state file..."
	zstd -kf dist/alpine-state.bin
	@echo "Build complete!"

run:
	@echo "Starting HTTP server on http://localhost:8000"
	python -m http.server

clean:
	@echo "Cleaning build artifacts..."
	rm -rf dist/alpine-rootfs.tar
	rm -rf dist/alpine-rootfs-flat
	rm -rf dist/alpine-fs.json
	rm -rf dist/alpine-state.bin
	rm -rf dist/alpine-state.bin.zst
	@echo "Clean complete!"
