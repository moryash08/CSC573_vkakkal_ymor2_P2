#!/usr/bin/env python3
"""Automation harness for Go-back-N experiments.

Runs the Simple FTP client/server for the three required experiments:
- Window size sweep (fixed MSS=500, p=0.05)
- MSS sweep (fixed N=64, p=0.05)
- Loss probability sweep (fixed N=64, MSS=500)

The script:
- Starts a fresh server for every trial.
- Measures RTT via traceroute (or ping fallback) once per run.
- Captures client transfer stats (bytes, segments, delay).
- Writes raw and averaged CSVs plus PNG plots.
"""

from __future__ import annotations

import argparse
import csv
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
import matplotlib.pyplot as plt


@dataclass
class TrialResult:
    experiment: str
    parameter_name: str
    parameter_value: float
    run_index: int
    duration_s: float
    bytes_sent: int
    segments_sent: int
    rtt_ms: Optional[float]

    def as_row(self) -> Dict[str, object]:
        return {
            "experiment": self.experiment,
            "parameter": self.parameter_name,
            "value": self.parameter_value,
            "run_index": self.run_index,
            "duration_s": self.duration_s,
            "bytes_sent": self.bytes_sent,
            "segments_sent": self.segments_sent,
            "rtt_ms": self.rtt_ms,
        }


def measure_rtt(host: str) -> Optional[float]:
    """Return RTT in ms using traceroute (hop 1) or ping fallback."""

    try:
        traceroute_cmd = ["traceroute", "-q", "1", "-m", "1", host]
        traceroute = subprocess.run(
            traceroute_cmd, capture_output=True, text=True, check=False
        )
        if traceroute.returncode == 0 and traceroute.stdout:
            match = re.search(r"(\d+(?:\.\d+)?)\s*ms", traceroute.stdout)
            if match:
                return float(match.group(1))
    except FileNotFoundError:
        traceroute = None

    try:
        ping_cmd = ["ping", "-c", "4", host]
        ping = subprocess.run(ping_cmd, capture_output=True, text=True, check=False)
        if ping.returncode == 0 and ping.stdout:
            match = re.search(r"=\s*[\d./]+\s*/\s*([\d.]+)\s*/", ping.stdout)
            if match:
                return float(match.group(1))
    except FileNotFoundError:
        ping = None

    return None


def start_server(
    python_executable: str,
    port: int,
    output_path: Path,
    loss_probability: float,
    log_path: Path,
) -> Tuple[subprocess.Popen, object]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "w", encoding="utf-8")
    cmd = [
        python_executable,
        "simple_ftp_server.py",
        str(port),
        str(output_path),
        str(loss_probability),
    ]
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)
    time.sleep(0.2)
    return proc, log_file


def stop_server(proc: subprocess.Popen, log_file: object) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)
    log_file.close()


def run_client(
    python_executable: str,
    host: str,
    port: int,
    file_path: Path,
    window_size: int,
    mss: int,
    timeout_s: float,
    log_path: Path,
) -> Tuple[int, int, float]:
    cmd = [
        python_executable,
        "simple_ftp_client.py",
        host,
        str(port),
        str(file_path),
        str(window_size),
        str(mss),
        "--timeout",
        str(timeout_s),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(result.stdout + result.stderr, encoding="utf-8")

    match = re.search(
        r"Transfer complete:\s+(\d+)\s+bytes across\s+(\d+)\s+segments in\s+([0-9.]+)\s+s",
        result.stdout,
    )
    if match is None:
        raise RuntimeError(
            f"Client run failed (window={window_size}, mss={mss}): {result.stdout}\n{result.stderr}"
        )

    bytes_sent = int(match.group(1))
    segments_sent = int(match.group(2))
    duration_s = float(match.group(3))
    return bytes_sent, segments_sent, duration_s


def run_trial(
    *,
    python_executable: str,
    host: str,
    port: int,
    file_path: Path,
    window_size: int,
    mss: int,
    loss_probability: float,
    timeout_s: float,
    run_index: int,
    experiment: str,
    parameter_name: str,
    parameter_value: float,
    scratch_dir: Path,
    logs_dir: Path,
) -> TrialResult:
    recv_path = scratch_dir / f"recv_{experiment}{parameter_value}{run_index}.txt"
    server_log = logs_dir / f"server_{experiment}{parameter_value}{run_index}.log"
    client_log = logs_dir / f"client_{experiment}{parameter_value}{run_index}.log"

    server_proc, server_log_handle = start_server(
        python_executable=python_executable,
        port=port,
        output_path=recv_path,
        loss_probability=loss_probability,
        log_path=server_log,
    )

    try:
        bytes_sent, segments_sent, duration_s = run_client(
            python_executable=python_executable,
            host=host,
            port=port,
            file_path=file_path,
            window_size=window_size,
            mss=mss,
            timeout_s=timeout_s,
            log_path=client_log,
        )
    finally:
        stop_server(server_proc, server_log_handle)

    rtt_ms = measure_rtt(host)

    print(
        f"[{experiment}] run={run_index} {parameter_name}={parameter_value} "
        f"bytes={bytes_sent} segments={segments_sent} duration={duration_s:.4f}s rtt_ms={rtt_ms}"
    )

    return TrialResult(
        experiment=experiment,
        parameter_name=parameter_name,
        parameter_value=parameter_value,
        run_index=run_index,
        duration_s=duration_s,
        bytes_sent=bytes_sent,
        segments_sent=segments_sent,
        rtt_ms=rtt_ms,
    )


def average_trials(trials: Iterable[TrialResult]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, str, float], List[TrialResult]] = {}
    for trial in trials:
        grouped.setdefault(
            (trial.experiment, trial.parameter_name, trial.parameter_value), []
        ).append(trial)

    averages: List[Dict[str, object]] = []
    for (experiment, parameter, value), items in grouped.items():
        durations = [t.duration_s for t in items]
        rtts = [t.rtt_ms for t in items if t.rtt_ms is not None]
        averages.append(
            {
                "experiment": experiment,
                "parameter": parameter,
                "value": value,
                "avg_duration_s": statistics.mean(durations),
                "min_duration_s": min(durations),
                "max_duration_s": max(durations),
                "avg_rtt_ms": statistics.mean(rtts) if rtts else None,
                "runs": len(items),
            }
        )
    return averages


def write_csv(path: Path, fieldnames: List[str], rows: Iterable[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def plot_series(
    output_path: Path,
    title: str,
    xlabel: str,
    ylabel: str,
    points: List[Tuple[float, float]],
) -> None:
    if plt is None:
        print("matplotlib not installed; skipping plot", file=sys.stderr)
        return

    points_sorted = sorted(points, key=lambda item: item[0])
    xs = [p[0] for p in points_sorted]
    ys = [p[1] for p in points_sorted]

    plt.figure()
    plt.plot(xs, ys, marker="o")
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.grid(True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, bbox_inches="tight")
    plt.close()


def run_experiments(
    *,
    python_executable: str,
    host: str,
    port: int,
    file_path: Path,
    runs: int,
    timeout_s: float,
    output_dir: Path,
) -> None:
    file_size = file_path.stat().st_size
    print(f"Using file '{file_path}' ({file_size} bytes)")
    print(f"Connecting to {host}:{port} with timeout={timeout_s}s")

    scratch_dir = output_dir / "scratch"
    logs_dir = output_dir / "logs"
    results: List[TrialResult] = []

    # Task 1: window sweep
    window_values = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]
    for window_size in window_values:
        for run_index in range(1, runs + 1):
            results.append(
                run_trial(
                    python_executable=python_executable,
                    host=host,
                    port=port,
                    file_path=file_path,
                    window_size=window_size,
                    mss=500,
                    loss_probability=0.05,
                    timeout_s=timeout_s,
                    run_index=run_index,
                    experiment="window_sweep",
                    parameter_name="N",
                    parameter_value=window_size,
                    scratch_dir=scratch_dir,
                    logs_dir=logs_dir,
                )
            )

    # Task 2: MSS sweep
    for mss in range(100, 1001, 100):
        for run_index in range(1, runs + 1):
            results.append(
                run_trial(
                    python_executable=python_executable,
                    host=host,
                    port=port,
                    file_path=file_path,
                    window_size=64,
                    mss=mss,
                    loss_probability=0.05,
                    timeout_s=timeout_s,
                    run_index=run_index,
                    experiment="mss_sweep",
                    parameter_name="MSS",
                    parameter_value=mss,
                    scratch_dir=scratch_dir,
                    logs_dir=logs_dir,
                )
            )

    # Task 3: loss probability sweep
    loss_values = [round(x / 100, 2) for x in range(1, 11)]
    for loss_probability in loss_values:
        for run_index in range(1, runs + 1):
            results.append(
                run_trial(
                    python_executable=python_executable,
                    host=host,
                    port=port,
                    file_path=file_path,
                    window_size=64,
                    mss=500,
                    loss_probability=loss_probability,
                    timeout_s=timeout_s,
                    run_index=run_index,
                    experiment="loss_sweep",
                    parameter_name="p",
                    parameter_value=loss_probability,
                    scratch_dir=scratch_dir,
                    logs_dir=logs_dir,
                )
            )

    raw_csv = output_dir / "raw_trials.csv"
    write_csv(
        raw_csv,
        ["experiment", "parameter", "value", "run_index", "duration_s", "bytes_sent", "segments_sent", "rtt_ms"],
        (trial.as_row() for trial in results),
    )

    averages = average_trials(results)
    avg_csv = output_dir / "averages.csv"
    write_csv(
        avg_csv,
        ["experiment", "parameter", "value", "avg_duration_s", "min_duration_s", "max_duration_s", "avg_rtt_ms", "runs"],
        averages,
    )

    print(f"Wrote raw trial data to {raw_csv}")
    print(f"Wrote averaged results to {avg_csv}")

    # Build plots
    avg_map: Dict[Tuple[str, float], float] = {
        (row["experiment"], float(row["value"])): float(row["avg_duration_s"])
        for row in averages
        if row.get("avg_duration_s") is not None
    }
    plot_series(
        output_path=output_dir / "plot_window_vs_delay.png",
        title="Average Delay vs Window Size",
        xlabel="Window size N",
        ylabel="Average delay (s)",
        points=[(n, avg_map[("window_sweep", float(n))]) for n in window_values],
    )
    plot_series(
        output_path=output_dir / "plot_mss_vs_delay.png",
        title="Average Delay vs MSS",
        xlabel="MSS (bytes)",
        ylabel="Average delay (s)",
        points=[(mss, avg_map[("mss_sweep", float(mss))]) for mss in range(100, 1001, 100)],
    )
    plot_series(
        output_path=output_dir / "plot_loss_vs_delay.png",
        title="Average Delay vs Loss Probability",
        xlabel="Loss probability p",
        ylabel="Average delay (s)",
        points=[(p, avg_map[("loss_sweep", float(p))]) for p in loss_values],
    )


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Go-back-N experiments automatically")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Server host/IP (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7735,
        help="UDP port for the server (default: 7735)",
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to the file to transfer in all experiments",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=5,
        help="Trials per parameter value (default: 5)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.2,
        help="Client retransmission timeout in seconds (default: 0.2)",
    )
    parser.add_argument(
        "--output-dir",
        default="experiment_results",
        help="Directory for CSVs, plots, and logs (default: experiment_results)",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter to run client/server (default: current interpreter)",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> None:
    args = parse_args(argv)
    file_path = Path(args.file).expanduser().resolve()
    if not file_path.exists():
        raise FileNotFoundError(file_path)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    run_experiments(
        python_executable=args.python,
        host=args.host,
        port=args.port,
        file_path=file_path,
        runs=args.runs,
        timeout_s=args.timeout,
        output_dir=output_dir,
    )

if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        print("Interrupted by user", file=sys.stderr)