#!/usr/bin/env python3
"""Simple-FTP client that implements Go-back-N over UDP."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from typing import List, Tuple

from simple_ftp_common import build_data_packet, parse_ack_packet


def load_segments(file_path: str, mss: int) -> Tuple[List[bytes], int]:
    packets: List[bytes] = []
    total_bytes = 0
    sequence = 0

    with open(file_path, "rb") as handle:
        while True:
            chunk = handle.read(mss)
            if not chunk:
                break
            packets.append(build_data_packet(sequence, chunk))
            sequence += 1
            total_bytes += len(chunk)

    return packets, total_bytes


def send_file(
    server_host: str,
    server_port: int,
    file_path: str,
    window_size: int,
    mss: int,
    timeout_interval: float,
) -> None:
    if window_size <= 0:
        raise ValueError("Window size N must be positive")
    if mss <= 0:
        raise ValueError("MSS must be positive")

    packets, total_bytes = load_segments(file_path, mss)
    total_segments = len(packets)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.connect((server_host, server_port))

    base = 0
    next_seq = 0
    timer_start: float | None = None
    start_time = time.monotonic()

    try:
        while base < total_segments:
            while next_seq < total_segments and next_seq < base + window_size:
                sock.send(packets[next_seq])
                if timer_start is None:
                    timer_start = time.monotonic()
                next_seq += 1
            if base == next_seq:
                timer_start = None
                continue

            assert timer_start is not None
            elapsed = time.monotonic() - timer_start
            time_left = timeout_interval - elapsed
            timed_out = False

            if time_left <= 0:
                timed_out = True
            else:
                sock.settimeout(time_left)
                try:
                    ack_data = sock.recv(1024)
                except socket.timeout:
                    timed_out = True
                else:
                    ack_sequence = parse_ack_packet(ack_data)
                    if ack_sequence is None or ack_sequence < base:
                        continue
                    base = min(ack_sequence + 1, total_segments)
                    timer_start = None if base == next_seq else time.monotonic()
                    continue

            if timed_out:
                print(f"Timeout, sequence number = {base}")
                for seq in range(base, next_seq):
                    sock.send(packets[seq])
                timer_start = time.monotonic() if base != next_seq else None
                continue
    finally:
        sock.close()

    duration = time.monotonic() - start_time
    print(
        f"Transfer complete: {total_bytes} bytes across {total_segments} segments in {duration:.3f} s."
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple-FTP Go-back-N client")
    parser.add_argument("server_host", help="Server host name or IP address")
    parser.add_argument("server_port", type=int, help="Server UDP port (7735)")
    parser.add_argument("file_name", help="Path to the file being transferred")
    parser.add_argument("window_size", type=int, help="Go-back-N window size N")
    parser.add_argument("mss", type=int, help="Maximum Segment Size (bytes)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.2,
        help="Retransmission timeout in seconds (default: 0.2)",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> None:
    args = parse_args(argv)
    if not os.path.exists(args.file_name):
        raise FileNotFoundError(args.file_name)

    send_file(
        server_host=args.server_host,
        server_port=args.server_port,
        file_path=args.file_name,
        window_size=args.window_size,
        mss=args.mss,
        timeout_interval=args.timeout,
    )


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        print("Interrupted by user", file=sys.stderr)
