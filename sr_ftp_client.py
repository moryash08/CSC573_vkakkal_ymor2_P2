#!/usr/bin/env python3
"""Selective Repeat Simple-FTP client."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from typing import Dict, List, Optional, Tuple

from simple_ftp_common import build_control_packet, build_data_packet, parse_ack_packet


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
            total_bytes += len(chunk)
            sequence += 1

    return packets, total_bytes


def compute_receiver_window(window_size: int, mss: int, ack_buffer_bytes: Optional[int]) -> int:
    if ack_buffer_bytes is None or ack_buffer_bytes <= 0:
        return max(1, window_size)
    capacity = max(1, ack_buffer_bytes // max(1, mss))
    return max(1, min(window_size, capacity))


def send_file(
    server_host: str,
    server_port: int,
    file_path: str,
    window_size: int,
    mss: int,
    timeout_interval: float,
    ack_buffer_bytes: Optional[int],
) -> None:
    if window_size <= 0:
        raise ValueError("Window size N must be positive")
    if mss <= 0:
        raise ValueError("MSS must be positive")

    packets, total_bytes = load_segments(file_path, mss)
    total_segments = len(packets)

    receiver_window = compute_receiver_window(window_size, mss, ack_buffer_bytes)
    effective_window = min(window_size, receiver_window)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.connect((server_host, server_port))
    sock.send(build_control_packet("WINDOW", float(receiver_window)))

    base = 0
    next_seq = 0
    acked: Dict[int, bool] = {}
    send_times: Dict[int, float] = {}
    start_time = time.monotonic()

    try:
        while base < total_segments:
            while next_seq < total_segments and next_seq < base + effective_window:
                sock.send(packets[next_seq])
                send_times[next_seq] = time.monotonic()
                acked.setdefault(next_seq, False)
                next_seq += 1

            # Attempt to process an ACK.
            sock.settimeout(0.05)
            try:
                ack_data = sock.recv(1024)
            except socket.timeout:
                pass
            else:
                ack_sequence = parse_ack_packet(ack_data)
                if ack_sequence is not None and 0 <= ack_sequence < total_segments:
                    acked[ack_sequence] = True
                    send_times.pop(ack_sequence, None)
                    if ack_sequence == base:
                        while acked.get(base):
                            acked.pop(base, None)
                            base += 1
                        # Clean up timers for everything below base.
                        for obsolete in [seq for seq in send_times if seq < base]:
                            send_times.pop(obsolete, None)
                    continue

            # Handle per-packet timeouts.
            now = time.monotonic()
            for seq, sent_time in list(send_times.items()):
                if now - sent_time >= timeout_interval:
                    print(f"Timeout, sequence number = {seq}")
                    sock.send(packets[seq])
                    send_times[seq] = time.monotonic()

    finally:
        sock.close()

    duration = time.monotonic() - start_time
    print(
        f"Transfer complete: {total_bytes} bytes across {total_segments} segments in {duration:.3f} s."
    )


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Selective Repeat Simple-FTP client")
    parser.add_argument("server_host", help="Server host name or IP address")
    parser.add_argument("server_port", type=int, help="Server UDP port (7735)")
    parser.add_argument("file_name", help="Path to the file being transferred")
    parser.add_argument("window_size", type=int, help="Selective Repeat window size N")
    parser.add_argument("mss", type=int, help="Maximum Segment Size (bytes)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.2,
        help="Retransmission timeout per segment (seconds, default: 0.2)",
    )
    parser.add_argument(
        "--ack-buffer-bytes",
        type=int,
        default=0,
        help="Approximate number of bytes the receiver can dedicate to outstanding ACKs; "
        "if > 0 the server window is capped at floor(buffer / MSS). Default 0 (match sender window).",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> None:
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
        ack_buffer_bytes=args.ack_buffer_bytes if args.ack_buffer_bytes > 0 else None,
    )


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        print("Interrupted by user", file=sys.stderr)
