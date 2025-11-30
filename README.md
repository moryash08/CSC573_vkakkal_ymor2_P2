# Simple-FTP (Go-back-N)

This repository implements the transport-layer logic required for the CSC573 Simple-FTP project. The client and server communicate over UDP and overlay a Go-back-N (GBN) automatic repeat request protocol to guarantee reliable data transfer.  Both executables are written in Python 3.

## Setup (VCL + Laptop)

This guide navigates the final steps to open UDP on the VCL VM and verify reachability, and run your Simple FTP tests between your laptop (client) and the VCL host (server).

### Assumptions

- VCL host: `152.7.176.134`, user `ymor2`
- UDP port: `7735`
- Repo files copied to `/home/ymor2` on the VCL host
- Server &rarr; VCL host
- Client &rarr; Local Machine
- Python 3 available on both machines

### 1) Allow UDP 7735 on the VCL Host

Run on the VCL host (SSH):

```
sudo iptables -I INPUT 1 -p udp --dport 7735 -j ACCEPT
```

Note: This rule is not persistent across reboot; re-run after a restart. Confirm the rule:

```
sudo iptables -L INPUT -n --line-numbers | head -20
```

### 2) Verify UDP reachability

In one VCL shell, start a one-off listener:

```
python3 - <<'PY'
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("", 7735))
print("listening on 7735...")
data, addr = sock.recvfrom(2048)
print("got", len(data), "bytes from", addr, ":", data)
PY
```

From your laptop, send a test packet, needs ncat:

```
echo "udp test" | ncat -u 152.7.176.134 7735
```

You should see the message printed in the VCL shell. Testing Complete.

## Packet format

* Data segment header: 32-bit sequence number, 16-bit checksum (computed like the UDP checksum over the payload only), and a 16-bit marker.
* ACK segment header: 32-bit sequence number being acknowledged, 16-bit zero field, and a 16-bit marker.

The helper utilities live in `simple_ftp_common.py` and are imported by both
the server and the client.

## Running the server

Run this command on the VCL host (e.g., SSH into `152.7.176.134` as `ymor2`):

```
python simple_ftp_server.py 7735 received.txt 0.05 --scratch-dir scratch_sessions
```

Arguments:

1. UDP port to listen on (7735 for the project spec).
2. Output file that stores the received bytes (overwritten on start).
3. Packet loss probability `p` (0 ≤ p < 1).  Whenever a packet with sequence
   number `X` is dropped by the probabilistic service, the server prints `Packet loss, sequence number = X`.

Optional flag:

* `--scratch-dir <path>` saves each new transfer as `session_<n>.bin` under the given directory instead of overwriting the same file.

Press `Ctrl+C` to stop the server after a transfer.  The server acknowledges
duplicate packets and retransmits the last in-order ACK when needed to match
the textbook Go-back-N behavior.  When a new sender (IP, UDP port) contacts the
server or the same sender restarts after being idle for a moment, the server
automatically resets its receive window and truncates the output file so that
you can run multiple transfers without manually restarting the process.

## Running the client

```
python simple_ftp_client.py 152.7.176.134 7735 test_file.txt 64 500 --timeout 0.2
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

## Experiments

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

## Automated experiments

`run_experiments.py` can run all three tasks end-to-end assuming the
Simple-FTP server is already running on the remote host. On the VCL VM start the server once and point it at a scratch directory so each transfer is saved as a separate file:

```
python simple_ftp_server.py 7735 received.txt 0.05 --scratch-dir scratch_sessions
```

Then kick off the automation from your laptop (adjust host/file paths as needed):

```
python run_experiments.py --host 152.7.176.134 --port 7735 --file test_file.txt --output-dir output
```

The script executes the trials for each task, captures the client timings, and
writes `output/raw_trials.csv` plus `output/averages.csv`; plots are emitted into
the same `output` folder. 

If you already have CSVs and only need the charts, run:

```
python plot_results.py --averages-csv output/averages.csv --output-dir output
```

to regenerate the Task 1–3 PNGs without rerunning the transfers.
