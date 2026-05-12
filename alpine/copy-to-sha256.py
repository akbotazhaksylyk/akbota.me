#!/usr/bin/env python3

import os
import logging
import stat
import argparse
import hashlib
import shutil
import tarfile
import sys
import io
import importlib
import multiprocessing
import queue
import time
from dataclasses import dataclass
from typing import Optional

HASH_LENGTH = 64  # Full SHA-256 hash - zero collision risk

def hash_file(filename) -> str:
    with open(filename, "rb", buffering=0) as f:
        return hash_fileobj(f)

def hash_fileobj(f) -> str:
    h = hashlib.sha256()
    for b in iter(lambda: f.read(128*1024), b""):
        h.update(b)
    return h.hexdigest()

class ZstdCompress():
    def __init__(self):
        self.zstd_lib = None
        self.zstd_builtin = False

        if sys.version_info >= (3, 14):
            self.zstd_lib = importlib.import_module("compression").zstd
            self.zstd_builtin = True
        else:
            try:
                self.zstd_lib = importlib.import_module("zstandard")
                self.zstd_builtin = False
            except ImportError:
                print("Error: zstandard module required when using --zstd flag")
                print("Install with: pip install zstandard")
                sys.exit(1)

    def compress(self, src, dst_path):
        if self.zstd_builtin:
            with src as src_file, self.zstd_lib.open(dst_path, 'wb', level=19) as dst_file:
                shutil.copyfileobj(src_file, dst_file)
        else:
            with src as src_file, open(dst_path, 'wb') as dst_file:
                self.zstd_lib.ZstdCompressor(level=19).copy_stream(src_file, dst_file)

@dataclass
class WorkItem:
    member_name: str
    file_hash: str
    to_abs: str
    data: bytes

@dataclass
class WorkerStats:
    worker_id: int
    files_processed: int = 0
    bytes_processed: int = 0

def compression_worker(worker_id: int, work_queue: multiprocessing.Queue,
                      stats_queue: multiprocessing.Queue, zstd_builtin: bool):
    """Worker process that consumes files from queue and compresses them."""
    # Initialize zstd in worker process
    zstd_module = ZstdCompress()
    stats = WorkerStats(worker_id=worker_id)

    while True:
        try:
            item = work_queue.get(timeout=1)
            if item is None:  # Poison pill
                break

            # Compress the file
            src = io.BytesIO(item.data)
            zstd_module.compress(src, item.to_abs)

            stats.files_processed += 1
            stats.bytes_processed += len(item.data)

        except queue.Empty:
            continue
        except Exception as e:
            print(f"Worker {worker_id} error: {e}")
            continue

    stats_queue.put(stats)

def main():
    logging.basicConfig(format="%(message)s")
    logger = logging.getLogger("copy")
    logger.setLevel(logging.DEBUG)

    args = argparse.ArgumentParser(description="...",
                                   formatter_class=argparse.RawTextHelpFormatter)
    args.add_argument("from_path", metavar="from", help="from")
    args.add_argument("to_path", metavar="to", help="to")
    args.add_argument("--zstd", action="store_true", help="Use Zstandard compression")
    args.add_argument("-j", "--jobs", type=int, default=None,
                     help="Number of parallel workers (default: number of CPU cores)")

    args = args.parse_args()

    from_path = os.path.normpath(args.from_path)
    to_path = os.path.normpath(args.to_path)

    # Determine number of workers
    num_workers = args.jobs if args.jobs else multiprocessing.cpu_count()

    # Import zstd only if compression is requested
    if args.zstd:
        zstd_module = ZstdCompress()
    else:
        zstd_module = None

    if os.path.isfile(from_path):
        tar = tarfile.open(from_path, "r")
    else:
        tar = None

    if tar:
        handle_tar(logger, tar, to_path, args.zstd, zstd_module, num_workers)
    else:
        handle_dir(logger, from_path, to_path, args.zstd, zstd_module, num_workers)

def handle_dir(logger, from_path: str, to_path: str, use_compression: bool, zstd_module, num_workers: int):
    def onerror(oserror):
        logger.warning(oserror)

    # Track full hashes to detect collisions in short hash
    filename_to_hash = {}

    if not use_compression or num_workers == 1:
        # Single-threaded fallback
        files = os.walk(from_path, onerror=onerror)

        for f in files:
            dirpath, dirnames, filenames = f

            for filename in filenames:
                absname = os.path.join(dirpath, filename)
                st = os.lstat(absname)
                mode = st.st_mode

                assert not stat.S_ISDIR(mode)
                if stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
                    continue

                file_hash = hash_file(absname)
                filename = file_hash[0:HASH_LENGTH] + (".bin.zst" if use_compression else ".bin")
                to_abs = os.path.join(to_path, filename)

                # Check for hash collision
                existing = filename_to_hash.get(filename)
                if existing is not None and existing != file_hash:
                    raise RuntimeError(f"HASH COLLISION DETECTED! {filename}: {existing} vs {file_hash}")
                filename_to_hash[filename] = file_hash

                if os.path.exists(to_abs):
                    logger.info("Exists, skipped {} ({})".format(to_abs, absname))
                else:
                    if use_compression:
                        logger.info("Compressing {} {}".format(absname, to_abs))
                        zstd_module.compress(open(absname, 'rb'), to_abs)
                    else:
                        logger.info("cp {} {}".format(absname, to_abs))
                        shutil.copyfile(absname, to_abs)
        return

    # Parallel compression
    logger.info(f"Using {num_workers} workers for parallel compression")

    work_queue = multiprocessing.Queue(maxsize=num_workers * 2)
    stats_queue = multiprocessing.Queue()

    # Start worker processes
    workers = []
    for i in range(num_workers):
        p = multiprocessing.Process(
            target=compression_worker,
            args=(i, work_queue, stats_queue, zstd_module.zstd_builtin)
        )
        p.start()
        workers.append(p)

    # Enqueue work items
    files_to_compress = 0
    start_time = time.time()
    files = os.walk(from_path, onerror=onerror)

    for f in files:
        dirpath, dirnames, filenames = f

        for filename in filenames:
            absname = os.path.join(dirpath, filename)
            st = os.lstat(absname)
            mode = st.st_mode

            assert not stat.S_ISDIR(mode)
            if stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
                continue

            file_hash = hash_file(absname)
            filename = file_hash[0:HASH_LENGTH] + (".bin.zst" if use_compression else ".bin")
            to_abs = os.path.join(to_path, filename)

            # Check for hash collision
            existing = filename_to_hash.get(filename)
            if existing is not None and existing != file_hash:
                raise RuntimeError(f"HASH COLLISION DETECTED! {filename}: {existing} vs {file_hash}")
            filename_to_hash[filename] = file_hash

            if os.path.exists(to_abs):
                logger.info("Exists, skipped {} ({})".format(to_abs, absname))
            else:
                logger.info("Queuing for compression {} ({})".format(to_abs, absname))
                with open(absname, 'rb') as file:
                    data = file.read()
                work_item = WorkItem(
                    member_name=absname,
                    file_hash=file_hash,
                    to_abs=to_abs,
                    data=data
                )
                work_queue.put(work_item)
                files_to_compress += 1

    # Send poison pills
    for _ in range(num_workers):
        work_queue.put(None)

    # Wait for workers to finish
    for p in workers:
        p.join()

    # Collect statistics
    worker_stats = []
    while not stats_queue.empty():
        worker_stats.append(stats_queue.get())

    elapsed = time.time() - start_time

    # Print statistics
    print("\n" + "="*70)
    print("COMPRESSION STATISTICS")
    print("="*70)
    print(f"Total time: {elapsed:.2f}s")
    print(f"Workers: {num_workers}")
    print(f"Files compressed: {files_to_compress}")
    print("-"*70)

    total_files = sum(s.files_processed for s in worker_stats)
    total_bytes = sum(s.bytes_processed for s in worker_stats)

    worker_stats.sort(key=lambda s: s.worker_id)

    print(f"{'Worker':<10} {'Files':<15} {'Size (MB)':<15} {'Files %':<12} {'Size %':<12}")
    print("-"*70)

    for stats in worker_stats:
        files_pct = (stats.files_processed / total_files * 100) if total_files > 0 else 0
        bytes_pct = (stats.bytes_processed / total_bytes * 100) if total_bytes > 0 else 0
        size_mb = stats.bytes_processed / (1024 * 1024)

        print(f"Worker {stats.worker_id:<3} {stats.files_processed:<15} "
              f"{size_mb:<15.2f} {files_pct:<12.1f} {bytes_pct:<12.1f}")

    print("-"*70)
    print(f"{'TOTAL':<10} {total_files:<15} {total_bytes/(1024*1024):<15.2f} "
          f"{'100.0':<12} {'100.0':<12}")
    print("="*70 + "\n")

def handle_tar(logger, tar, to_path: str, use_compression: bool, zstd_module, num_workers: int):
    # Track full hashes to detect collisions in short hash
    filename_to_hash = {}

    if not use_compression or num_workers == 1:
        # Single-threaded fallback
        for member in tar.getmembers():
            if member.isfile() or member.islnk():
                f = tar.extractfile(member)
                file_hash = hash_fileobj(f)
                filename = file_hash[0:HASH_LENGTH] + (".bin.zst" if use_compression else ".bin")
                to_abs = os.path.join(to_path, filename)

                # Check for hash collision
                existing = filename_to_hash.get(filename)
                if existing is not None and existing != file_hash:
                    raise RuntimeError(f"HASH COLLISION DETECTED! {filename}: {existing} vs {file_hash}")
                filename_to_hash[filename] = file_hash

                if os.path.exists(to_abs):
                    logger.info("Exists, skipped {} ({})".format(to_abs, member.name))
                else:
                    if use_compression:
                        logger.info("Extracted and compressing {} ({})".format(to_abs, member.name))
                        f.seek(0)
                        zstd_module.compress(f, to_abs)
                    else:
                        logger.info("Extracted {} ({})".format(to_abs, member.name))
                        to_file = open(to_abs, "wb")
                        f.seek(0)
                        shutil.copyfileobj(f, to_file)
        return

    # Parallel compression
    logger.info(f"Using {num_workers} workers for parallel compression")

    work_queue = multiprocessing.Queue(maxsize=num_workers * 2)
    stats_queue = multiprocessing.Queue()

    # Start worker processes
    workers = []
    for i in range(num_workers):
        p = multiprocessing.Process(
            target=compression_worker,
            args=(i, work_queue, stats_queue, zstd_module.zstd_builtin)
        )
        p.start()
        workers.append(p)

    # Enqueue work items
    files_to_compress = 0
    start_time = time.time()

    for member in tar.getmembers():
        if member.isfile() or member.islnk():
            f = tar.extractfile(member)
            file_hash = hash_fileobj(f)
            filename = file_hash[0:HASH_LENGTH] + (".bin.zst" if use_compression else ".bin")
            to_abs = os.path.join(to_path, filename)

            # Check for hash collision
            existing = filename_to_hash.get(filename)
            if existing is not None and existing != file_hash:
                raise RuntimeError(f"HASH COLLISION DETECTED! {filename}: {existing} vs {file_hash}")
            filename_to_hash[filename] = file_hash

            if os.path.exists(to_abs):
                logger.info("Exists, skipped {} ({})".format(to_abs, member.name))
            else:
                logger.info("Queuing for compression {} ({})".format(to_abs, member.name))
                f.seek(0)
                data = f.read()
                work_item = WorkItem(
                    member_name=member.name,
                    file_hash=file_hash,
                    to_abs=to_abs,
                    data=data
                )
                work_queue.put(work_item)
                files_to_compress += 1

    # Send poison pills
    for _ in range(num_workers):
        work_queue.put(None)

    # Wait for workers to finish
    for p in workers:
        p.join()

    # Collect statistics
    worker_stats = []
    while not stats_queue.empty():
        worker_stats.append(stats_queue.get())

    elapsed = time.time() - start_time

    # Print statistics
    print("\n" + "="*70)
    print("COMPRESSION STATISTICS")
    print("="*70)
    print(f"Total time: {elapsed:.2f}s")
    print(f"Workers: {num_workers}")
    print(f"Files compressed: {files_to_compress}")
    print("-"*70)

    total_files = sum(s.files_processed for s in worker_stats)
    total_bytes = sum(s.bytes_processed for s in worker_stats)

    worker_stats.sort(key=lambda s: s.worker_id)

    print(f"{'Worker':<10} {'Files':<15} {'Size (MB)':<15} {'Files %':<12} {'Size %':<12}")
    print("-"*70)

    for stats in worker_stats:
        files_pct = (stats.files_processed / total_files * 100) if total_files > 0 else 0
        bytes_pct = (stats.bytes_processed / total_bytes * 100) if total_bytes > 0 else 0
        size_mb = stats.bytes_processed / (1024 * 1024)

        print(f"Worker {stats.worker_id:<3} {stats.files_processed:<15} "
              f"{size_mb:<15.2f} {files_pct:<12.1f} {bytes_pct:<12.1f}")

    print("-"*70)
    print(f"{'TOTAL':<10} {total_files:<15} {total_bytes/(1024*1024):<15.2f} "
          f"{'100.0':<12} {'100.0':<12}")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
