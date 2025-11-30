#!/usr/bin/env python3
"""Simple-FTP server that implements the Go-back-N receiver."""

from __future__ import annotations

import argparse
import os
import random
import socket
import sys
import time
from typing import Optional, Tuple

from simple_ftp_common import (
    DATA_PACKET_TYPE,
    build_ack_packet,
    calculate_checksum,
    parse_data_packet,
)


def run_server(port: int, output_path: str, loss_probability: float) -> None:
    if not (0.0 <= loss_probability < 1.0):
        raise ValueError("loss probability p must be in [0, 1)")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", port))

    expected_sequence = 0
    current_client: Optional[Tuple[str, int]] = None
    last_packet_time = 0.0

    print(f"Listening on UDP port {port}, writing to '{output_path}'")
    print(f"Packet loss probability = {loss_probability}")

    with open(output_path, "wb") as output_file:

        def reset_session(client_address: Tuple[str, int]) -> None:
            nonlocal expected_sequence, current_client
            output_file.seek(0)
            output_file.truncate()
            expected_sequence = 0
            current_client = client_address
            print(f"New transfer from {client_address}")

        try:
            while True:
                data, client_address = sock.recvfrom(65535)

                parsed = parse_data_packet(data)
                if not parsed:
                    continue

                sequence, checksum, packet_type, payload = parsed
                now = time.monotonic()

                if current_client is None or client_address != current_client:
                    reset_session(client_address)
                elif (
                    sequence == 0
                    and expected_sequence != 0
                    and now - last_packet_time > 1.0
                ):
                    # Same sender restarted a new transfer after some idle time.
                    reset_session(client_address)

                last_packet_time = now
                if packet_type != DATA_PACKET_TYPE:
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
                    output_file.write(payload)
                    output_file.flush()
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
    return parser.parse_args(argv)


def main(argv: list[str]) -> None:
    args = parse_args(argv)
    directory = os.path.dirname(os.path.abspath(args.file_name))
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

    run_server(port=args.port, output_path=args.file_name, loss_probability=args.p)


if __name__ == "__main__":
    main(sys.argv[1:])
