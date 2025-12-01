#!/usr/bin/env python3
"""Selective Repeat Simple-FTP server."""

from __future__ import annotations

import argparse
import random
import socket
import sys
import time
from pathlib import Path
from typing import Dict, IO, List, Optional, Tuple

from simple_ftp_common import (
    CONTROL_PACKET_TYPE,
    DATA_PACKET_TYPE,
    build_ack_packet,
    calculate_checksum,
    parse_control_payload,
    parse_data_packet,
)


def run_server(
    port: int,
    output_path: Path,
    window_size: int,
    loss_probability: float,
    scratch_dir: Optional[Path] = None,
) -> None:
    if not (0.0 <= loss_probability < 1.0):
        raise ValueError("loss probability p must be in [0, 1)")
    if window_size <= 0:
        raise ValueError("Window size must be positive")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", port))

    if scratch_dir:
        scratch_dir.mkdir(parents=True, exist_ok=True)

    expected_base = 0
    buffer: Dict[int, bytes] = {}
    current_client: Optional[Tuple[str, int]] = None
    current_file: Optional[IO[bytes]] = None
    session_counter = 0
    receiver_window = window_size
    last_packet_time = 0.0

    def close_file() -> None:
        nonlocal current_file
        if current_file is not None:
            current_file.close()
            current_file = None

    def open_session_file(client_address: Tuple[str, int]) -> None:
        nonlocal current_client, expected_base, buffer, current_file, session_counter, last_packet_time
        close_file()
        buffer = {}
        expected_base = 0
        session_counter += 1
        if scratch_dir:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            session_path = scratch_dir / f"session_sr_{session_counter}_{timestamp}.bin"
        else:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            session_path = output_path
        current_file = session_path.open("wb")
        current_client = client_address
        last_packet_time = time.monotonic()
        print(f"New SR transfer from {client_address}, writing to '{session_path}'")
        return

    def deliver_in_order(file_handle: IO[bytes]) -> None:
        nonlocal expected_base, buffer
        while expected_base in buffer:
            file_handle.write(buffer.pop(expected_base))
            file_handle.flush()
            expected_base += 1

    try:
        print(f"Selective Repeat server listening on UDP port {port}")
        print(f"Initial loss probability = {loss_probability}, window size = {receiver_window}")

        while True:
            data, client_address = sock.recvfrom(65535)
            parsed = parse_data_packet(data)
            if not parsed:
                continue

            sequence, checksum, packet_type, payload = parsed

            now = time.monotonic()

            if packet_type == CONTROL_PACKET_TYPE:
                parsed_control = parse_control_payload(payload)
                if not parsed_control:
                    print("Received invalid control payload, ignoring")
                    continue
                command, value = parsed_control
                if command == "LOSS":
                    if not (0.0 <= value < 1.0):
                        print(f"Ignoring out-of-range loss probability request: {value}")
                        continue
                    loss_probability = value
                    print(f"Updated loss probability to {loss_probability}")
                elif command == "WINDOW":
                    new_window = int(max(1, value))
                    receiver_window = new_window
                    print(f"Updated receiver window size to {receiver_window}")
                else:
                    print(f"Ignoring unsupported control command '{command}'")
                continue

            if packet_type != DATA_PACKET_TYPE:
                continue

            if current_client is None or client_address != current_client:
                open_session_file(client_address)
            elif (
                sequence == 0
                and expected_base != 0
                and now - last_packet_time > 1.0
            ):
                # New transfer from same sender after idle period.
                open_session_file(client_address)

            if current_file is None:
                continue

            last_packet_time = now

            if random.random() <= loss_probability:
                print(f"Packet loss, sequence number = {sequence}")
                continue

            computed_checksum = calculate_checksum(payload)
            if computed_checksum != checksum:
                continue

            if sequence < expected_base:
                # Already delivered; re-ACK to help the sender.
                sock.sendto(build_ack_packet(sequence), client_address)
                continue

            if sequence >= expected_base + receiver_window:
                # Outside the receive window; ignore but ACK last valid sequence.
                if expected_base > 0:
                    sock.sendto(build_ack_packet(expected_base - 1), client_address)
                continue

            if sequence not in buffer:
                buffer[sequence] = payload

            sock.sendto(build_ack_packet(sequence), client_address)
            deliver_in_order(current_file)

    except KeyboardInterrupt:
        print("\nShutting down Selective Repeat server ...", file=sys.stderr)
    finally:
        close_file()
        sock.close()


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Selective Repeat Simple-FTP server")
    parser.add_argument("port", type=int, help="UDP port to listen on (7735)")
    parser.add_argument("file_name", help="File that will store the received data")
    parser.add_argument("window_size", type=int, help="Selective Repeat window size N")
    parser.add_argument(
        "p",
        type=float,
        help="Probability that an incoming packet is dropped prior to validation",
    )
    parser.add_argument(
        "--scratch-dir",
        help="Directory where each new transfer is saved as session_sr_<n>.bin "
        "(default: overwrite the provided file name)",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> None:
    args = parse_args(argv)
    output_path = Path(args.file_name).expanduser().resolve()
    if output_path.parent and not output_path.parent.exists():
        output_path.parent.mkdir(parents=True, exist_ok=True)

    scratch_dir = Path(args.scratch_dir).expanduser().resolve() if args.scratch_dir else None

    run_server(
        port=args.port,
        output_path=output_path,
        window_size=args.window_size,
        loss_probability=args.p,
        scratch_dir=scratch_dir,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
