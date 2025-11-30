# Simple-FTP (Go-back-N)

This repository implements the transport-layer logic required for the CSC573
Simple-FTP project.  The client and server communicate over UDP and overlay a
Go-back-N (GBN) automatic repeat request protocol to guarantee reliable data
transfer.  Both executables are written in Python 3.

## Packet format

* Data segment header: 32-bit sequence number, 16-bit checksum (computed like
  the UDP checksum over the payload only), and a 16-bit 0x5555 marker.
* ACK segment header: 32-bit sequence number being acknowledged, 16-bit zero
  field, and a 16-bit 0xAAAA marker.

The helper utilities live in `simple_ftp_common.py` and are imported by both
the server and the client.

## Running the server

```
python simple_ftp_server.py 7735 received.bin 0.05
```

Arguments:

1. UDP port to listen on (7735 for the project spec).
2. Output file that stores the received bytes (overwritten on start).
3. Packet loss probability `p` (0 ≤ p < 1).  Whenever a packet with sequence
   number `X` is dropped by the probabilistic service, the server prints
   `Packet loss, sequence number = X`.

Press `Ctrl+C` to stop the server after a transfer.  The server acknowledges
duplicate packets and retransmits the last in-order ACK when needed to match
the textbook Go-back-N behavior.  When a new sender (IP, UDP port) contacts the
server or the same sender restarts after being idle for a moment, the server
automatically resets its receive window and truncates the output file so that
you can run multiple transfers without manually restarting the process.

## Running the client

```
python simple_ftp_client.py server-host 7735 file_to_send.bin 64 500 --timeout 0.2
```

Arguments:

1. Hostname/IP address of the server.
2. UDP port (7735).
3. Path to the local file that will be transmitted.
4. Window size `N`.
5. Maximum Segment Size (MSS) in bytes (each segment carries exactly MSS bytes
   except possibly the final one).

Optional flag:

* `--timeout <seconds>` controls the retransmission timeout (default: 0.2 s).

Whenever a timeout happens for the oldest outstanding packet with sequence
number `Y`, the client prints `Timeout, sequence number = Y`.

## Suggested experiments

Use any file larger than 1 MB and run the programs on different hosts that are
separated by a few router hops.  The following loops satisfy Tasks 1–3 from the
project handout:

* **Task 1 (window size)** – run with `MSS = 500`, `p = 0.05`, and window size
  `N ∈ {1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024}`.  For each N perform five
  transfers, record the duration reported by the client, and compute the
  average delay.
* **Task 2 (MSS)** – run with `N = 64`, `p = 0.05`, and vary the MSS from 100
  to 1,000 bytes in increments of 100.  Again, transmit the file five times per
  configuration and average the delays.
* **Task 3 (loss probability)** – run with `N = 64`, `MSS = 500`, and vary `p`
  from 0.01 to 0.10 (step 0.01).  Transfer the file five times for each `p` and
  average the delays.

Record the average transfer times, file size, and RTT measurements as required
by the report.  Plot the results to study how each parameter affects the total
delay.
