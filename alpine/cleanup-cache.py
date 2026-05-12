#!/usr/bin/env python3
"""
Cleanup unused files from alpine-rootfs-flat directory.
Keeps only files referenced in alpine-fs.json.
"""

import json
import os
import sys
from pathlib import Path

def extract_filenames_from_node(node, filenames):
    """Recursively extract filenames from filesystem tree."""
    IDX_TARGET = 6
    IDX_FILENAME = 6

    if not isinstance(node, list):
        return

    for entry in node:
        if not isinstance(entry, list) or len(entry) < 7:
            continue

        target_or_filename = entry[IDX_TARGET]

        # If it's a filename (string ending with .bin or .bin.zst)
        if isinstance(target_or_filename, str):
            if target_or_filename.endswith('.bin') or target_or_filename.endswith('.bin.zst'):
                filenames.add(target_or_filename)
        # If it's a directory (list of children)
        elif isinstance(target_or_filename, list):
            extract_filenames_from_node(target_or_filename, filenames)

def main():
    if len(sys.argv) < 3:
        print("Usage: cleanup-cache.py <alpine-fs.json> <alpine-rootfs-flat-dir>")
        sys.exit(1)

    fs_json_path = sys.argv[1]
    rootfs_flat_dir = sys.argv[2]

    if not os.path.exists(fs_json_path):
        print(f"Error: {fs_json_path} not found")
        sys.exit(1)

    if not os.path.exists(rootfs_flat_dir):
        print(f"Warning: {rootfs_flat_dir} not found, nothing to cleanup")
        sys.exit(0)

    # Read alpine-fs.json and extract all referenced filenames
    print(f"Reading {fs_json_path}...")
    with open(fs_json_path, 'r') as f:
        fs_data = json.load(f)

    referenced_files = set()
    extract_filenames_from_node(fs_data.get('fsroot', []), referenced_files)

    print(f"Found {len(referenced_files)} referenced files in alpine-fs.json")

    # Get all files in alpine-rootfs-flat
    rootfs_flat_path = Path(rootfs_flat_dir)
    actual_files = set()
    for file_path in rootfs_flat_path.glob('*.bin*'):
        if file_path.is_file():
            actual_files.add(file_path.name)

    print(f"Found {len(actual_files)} files in {rootfs_flat_dir}")

    # Find files to delete (in directory but not referenced)
    files_to_delete = actual_files - referenced_files

    if not files_to_delete:
        print("No unused files to delete")
        return

    print(f"\nDeleting {len(files_to_delete)} unused files:")

    total_size_deleted = 0
    for filename in sorted(files_to_delete):
        file_path = rootfs_flat_path / filename
        file_size = file_path.stat().st_size
        total_size_deleted += file_size
        file_path.unlink()
        print(f"  Deleted: {filename} ({file_size / 1024:.1f} KB)")

    print(f"\nTotal space freed: {total_size_deleted / (1024 * 1024):.2f} MB")
    print(f"Remaining files: {len(actual_files) - len(files_to_delete)}")

if __name__ == "__main__":
    main()
