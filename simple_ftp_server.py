#!/usr/bin/env python3
"""Simple-FTP server that implements the Go-back-N receiver."""

from __future__ import annotations

import argparse
import random
import socket
import sys
import time
from pathlib import Path
from typing import IO, Optional, Tuple

from simple_ftp_common import (
    CONTROL_PACKET_TYPE,
    DATA_PACKET_TYPE,
    build_ack_packet,
    calculate_checksum,
    parse_data_packet,
)


def run_server(
    port: int,
    output_path: Path,
    loss_probability: float,
    scratch_dir: Optional[Path] = None,
) -> None:
    if not (0.0 <= loss_probability < 1.0):
        raise ValueError("loss probability p must be in [0, 1)")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", port))

    if scratch_dir:
        scratch_dir.mkdir(parents=True, exist_ok=True)

    expected_sequence = 0
    current_client: Optional[Tuple[str, int]] = None
    last_packet_time = 0.0
    current_file: Optional[IO[bytes]] = None
    current_path: Optional[Path] = None
    session_counter = 0

    print(f"Listening on UDP port {port}")
    print(f"Packet loss probability = {loss_probability}")

    def close_file() -> None:
        nonlocal current_file
        if current_file is not None:
            current_file.close()
            current_file = None

    def open_session_file(client_address: Tuple[str, int]) -> None:
        nonlocal expected_sequence, current_client, current_file, current_path, session_counter
        close_file()
        session_counter += 1
        if scratch_dir:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            current_path = scratch_dir / f"session_{session_counter}_{timestamp}.bin"
        else:
            current_path = output_path
            output_path.parent.mkdir(parents=True, exist_ok=True)

        current_file = current_path.open("wb")
        expected_sequence = 0
        current_client = client_address
        print(f"New transfer from {client_address}, writing to '{current_path}'")

    try:
        while True:
            data, client_address = sock.recvfrom(65535)

            parsed = parse_data_packet(data)
            if not parsed:
                continue

            sequence, checksum, packet_type, payload = parsed

            if packet_type == CONTROL_PACKET_TYPE:
                try:
                    requested = float(payload.decode("ascii").strip())
                except (ValueError, UnicodeDecodeError):
                    print("Received invalid control payload, ignoring")
                    continue
                if not (0.0 <= requested < 1.0):
                    print(f"Ignoring out-of-range loss probability request: {requested}")
                    continue
                loss_probability = requested
                print(f"Updated loss probability to {loss_probability}")
                continue

            now = time.monotonic()

            if current_client is None or client_address != current_client:
                open_session_file(client_address)
            elif (
                sequence == 0
                and expected_sequence != 0
                and now - last_packet_time > 1.0
            ):
                # Same sender restarted a new transfer after some idle time.
                open_session_file(client_address)

            last_packet_time = now
            if packet_type != DATA_PACKET_TYPE or current_file is None:
                continue

            if random.random() <= loss_probability:
                print(f"Packet loss, sequence number = {sequence}")
                continue

            computed_checksum = calculate_checksum(payload)
            if computed_checksum != checksum:
                # Corrupted packet -> ACK last in-order sequence.
                if expected_sequence > 0:
                    ack_packet = build_ack_packet(expected_sequence - 1)
                    sock.sendto(ack_packet, client_address)
                continue

            if sequence == expected_sequence:
                current_file.write(payload)
                current_file.flush()
                ack_packet = build_ack_packet(sequence)
                sock.sendto(ack_packet, client_address)
                expected_sequence += 1
            elif sequence < expected_sequence:
                # Duplicate segment due to timeout at sender.
                ack_packet = build_ack_packet(sequence)
                sock.sendto(ack_packet, client_address)
            else:
                # Future segment -> ACK last correctly received packet.
                if expected_sequence > 0:
                    ack_packet = build_ack_packet(expected_sequence - 1)
                    sock.sendto(ack_packet, client_address)
    except KeyboardInterrupt:
        print("\nShutting down server ...", file=sys.stderr)
    finally:
        close_file()
        sock.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple-FTP Go-back-N server")
    parser.add_argument("port", type=int, help="UDP port to listen on (7735)")
    parser.add_argument("file_name", help="File that will store the received data")
    parser.add_argument(
        "p",
        type=float,
        help="Probability that an incoming packet is dropped prior to validation",
    )
    parser.add_argument(
        "--scratch-dir",
        help="Directory where each new transfer is saved as session_<n>.bin "
        "(default: overwrite the provided file name)",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> None:
    args = parse_args(argv)
    output_path = Path(args.file_name).expanduser().resolve()
    if output_path.parent and not output_path.parent.exists():
        output_path.parent.mkdir(parents=True, exist_ok=True)

    scratch_dir = Path(args.scratch_dir).expanduser().resolve() if args.scratch_dir else None

    run_server(
        port=args.port,
        output_path=output_path,
        loss_probability=args.p,
        scratch_dir=scratch_dir,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
