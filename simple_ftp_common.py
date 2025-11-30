"""Shared helpers for the Simple-FTP Go-back-N implementation."""

from __future__ import annotations

import struct
from typing import Optional, Tuple


DATA_PACKET_TYPE = 0x5555
ACK_PACKET_TYPE = 0xAAAA
HEADER_STRUCT = struct.Struct("!IHH")
HEADER_SIZE = HEADER_STRUCT.size


def calculate_checksum(payload: bytes) -> int:
    """Compute the 16-bit one's complement checksum of *payload*."""

    total = 0
    length = len(payload)
    index = 0

    while index + 1 < length:
        total += (payload[index] << 8) + payload[index + 1]
        total = (total & 0xFFFF) + (total >> 16)
        index += 2

    if index < length:
        total += payload[index] << 8
        total = (total & 0xFFFF) + (total >> 16)

    return (~total) & 0xFFFF


def build_data_packet(sequence: int, payload: bytes) -> bytes:
    checksum = calculate_checksum(payload)
    header = HEADER_STRUCT.pack(sequence, checksum, DATA_PACKET_TYPE)
    return header + payload


def build_ack_packet(sequence: int) -> bytes:
    return HEADER_STRUCT.pack(sequence, 0, ACK_PACKET_TYPE)


def parse_data_packet(packet: bytes) -> Optional[Tuple[int, int, int, bytes]]:
    if len(packet) < HEADER_SIZE:
        return None
    sequence, checksum, packet_type = HEADER_STRUCT.unpack_from(packet)
    return sequence, checksum, packet_type, packet[HEADER_SIZE:]


def parse_ack_packet(packet: bytes) -> Optional[int]:
    parsed = parse_data_packet(packet)
    if not parsed:
        return None
    sequence, checksum, packet_type, _ = parsed
    if packet_type != ACK_PACKET_TYPE or checksum != 0:
        return None
    return sequence
