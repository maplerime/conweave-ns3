#!/usr/bin/env python3
"""
NS-3 Simulation Results Comparison Tool

Compares Hybrid vs Inflex load balancing modes across different FECMP_BG ratios.
Generates FCT performance tables and queue length statistics with percentage
changes relative to Hybrid(0) baseline (pure MoE traffic, no background flows).
"""

import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple
import numpy as np


class SimulationResult:
    """Store simulation results for a single configuration."""

    def __init__(self, lb_mode: int, fecmp_bg: int, path: str):
        self.lb_mode = lb_mode  # 0 = ECMP, 9 = Conweave, 10 = Hybrid, 12 = Inflex, 13 = Hybrid-AS, 14 = Hybrid-SS
        self.fecmp_bg = fecmp_bg
        self.path = path
        if lb_mode == 0:
            self.mode_name = "ECMP"
        elif lb_mode == 2:
            self.mode_name = f"Drill({fecmp_bg})" if fecmp_bg > 0 else "Drill(0)"
        elif lb_mode == 9:
            self.mode_name = "Conweave"  # No fecmp_bg suffix
        elif lb_mode == 10:
            self.mode_name = f"Hybrid({fecmp_bg})" if fecmp_bg > 0 else "Hybrid(0)"
        elif lb_mode == 12:
            self.mode_name = f"Inflex({fecmp_bg})" if fecmp_bg > 0 else "Inflex(0)"
        elif lb_mode == 13:
            self.mode_name = f"Hybrid-AS({fecmp_bg})" if fecmp_bg > 0 else "Hybrid-AS(0)"
        elif lb_mode == 14:
            self.mode_name = f"Hybrid-SS({fecmp_bg})" if fecmp_bg > 0 else "Hybrid-SS(0)"
        elif lb_mode == 16:
            self.mode_name = f"MixHash({fecmp_bg})" if fecmp_bg > 0 else "MixHash(0)"
        else:
            self.mode_name = f"Mode{lb_mode}({fecmp_bg})"

        # FCT metrics (in microseconds)
        self.avg_fct = 0.0
        self.p50_fct = 0.0
        self.p99_fct = 0.0
        self.stddev_fct = 0.0

        # Large flow FCT metrics (background flows, 8MB)
        self.large_avg_fct = 0.0
        self.large_p50_fct = 0.0
        self.large_p99_fct = 0.0
        self.large_total_flows = 0
        self.large_total_timeout = 0
        self.large_avg_timeout = 0.0
        self.large_max_timeout = 0

        # PFC count
        self.pfc_count = 0                    # Legacy: all PFC events
        self.pfc_count_small = 0              # PFC at small flow completion times
        self.pfc_count_large = 0              # All PFC events (for large flows)

        # Total flows count
        self.total_flows = 0

        # Timeout/Retransmission count
        self.total_timeout = 0
        self.avg_timeout = 0.0
        self.max_timeout = 0

        # Queue metrics (in bytes)
        self.avg_qlen = 0.0
        self.p50_qlen = 0.0
        self.p99_qlen = 0.0
        self.max_qlen = 0.0


def parse_config(config_path: str) -> Tuple[int, int]:
    """Parse config.txt to extract LB_MODE and FECMP_BG."""
    lb_mode = None
    fecmp_bg = None

    with open(config_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('LB_MODE'):
                lb_mode = int(line.split()[1])
            elif line.startswith('FECMP_BG'):
                fecmp_bg = int(line.split()[1])

            if lb_mode is not None and fecmp_bg is not None:
                break

    return lb_mode, fecmp_bg


def calculate_percentiles(data) -> Tuple[float, float]:
    """Calculate P50, P99 percentiles."""
    if len(data) == 0:
        return 0.0, 0.0
    sorted_data = np.sort(data)
    n = len(sorted_data)
    p50_idx = min(int(n * 0.5), n - 1)
    p99_idx = min(int(n * 0.99), n - 1)
    return float(sorted_data[p50_idx]), float(sorted_data[p99_idx])


def read_fct_results(fct_path: str) -> Tuple[float, float, float, float, int, float, int, int, float, float, float, int, int, float, int]:
    """Read FCT file and return avg, p50, p99, stddev in microseconds, and timeout stats.
    Only includes expert flows (8KB flows), excludes background flows (8MB).
    Also returns large flow (8MB background) FCT metrics.
    """
    fct_values = []
    timeout_counts = []
    large_fct_values = []
    large_timeout_counts = []
    EXPERT_FLOW_SIZE = 8192  # 8KB
    LARGE_FLOW_SIZE = 8388608  # 8MB

    with open(fct_path, 'r') as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 9:
                # Column 5 is flow size in bytes
                flow_size = int(parts[4])
                # Column 7 is FCT in nanoseconds
                fct_ns = float(parts[6])
                if flow_size == EXPERT_FLOW_SIZE:
                    # Expert flow (8KB)
                    fct_values.append(fct_ns)
                    # Column 9 is timeout count
                    timeout_counts.append(int(parts[8]))
                elif flow_size == LARGE_FLOW_SIZE:
                    # Large background flow (8MB)
                    large_fct_values.append(fct_ns)
                    large_timeout_counts.append(int(parts[8]))
            elif len(parts) >= 7:
                # Old format without timeout count
                flow_size = int(parts[4])
                fct_ns = float(parts[6])
                if flow_size == EXPERT_FLOW_SIZE:
                    fct_values.append(fct_ns)
                    timeout_counts.append(0)
                elif flow_size == LARGE_FLOW_SIZE:
                    large_fct_values.append(fct_ns)
                    large_timeout_counts.append(0)

    total_flows = len(fct_values)
    large_total_flows = len(large_fct_values)

    if not fct_values:
        return 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0, total_flows, 0.0, 0.0, 0.0, large_total_flows, 0.0, 0.0, 0

    fct_values = np.array(fct_values)
    avg_us = np.mean(fct_values) / 1000  # Convert to microseconds
    stddev_us = np.std(fct_values) / 1000

    p50_us, p99_us = calculate_percentiles(fct_values)
    p50_us /= 1000
    p99_us /= 1000

    # Timeout statistics
    total_timeout = sum(timeout_counts)
    avg_timeout = np.mean(timeout_counts) if timeout_counts else 0.0
    max_timeout = max(timeout_counts) if timeout_counts else 0

    # Large flow FCT statistics
    if large_fct_values:
        large_fct_values = np.array(large_fct_values)
        large_avg_us = np.mean(large_fct_values) / 1000
        large_p50_us, large_p99_us = calculate_percentiles(large_fct_values)
        large_p50_us /= 1000
        large_p99_us /= 1000
    else:
        large_avg_us = 0.0
        large_p50_us = 0.0
        large_p99_us = 0.0

    # Large flow timeout statistics
    large_total_timeout = sum(large_timeout_counts)
    large_avg_timeout = np.mean(large_timeout_counts) if large_timeout_counts else 0.0
    large_max_timeout = max(large_timeout_counts) if large_timeout_counts else 0

    return avg_us, p50_us, p99_us, stddev_us, total_timeout, avg_timeout, max_timeout, total_flows, large_avg_us, large_p50_us, large_p99_us, large_total_flows, large_total_timeout, large_avg_timeout, large_max_timeout


def read_pfc_count(pfc_path: str, fct_path: str = None,
                    small_flow_timestamps: list = None,
                    time_window_ns: int = 1000000) -> Tuple[int, int, int, int]:
    """Count PFC events with different methods for small and large flows.

    Args:
        pfc_path: Path to PFC file
        fct_path: Path to FCT file (optional, used to extract completion times)
        small_flow_timestamps: List of (start_time, completion_time) tuples (optional)
        time_window_ns: Time window in ns to match PFC events to completion times (default 1us)

    Returns:
        Tuple[int, int, int, int]: (small_flow_pfc_count, large_flow_pfc_count,
                                     small_flow_time_range_ns, large_flow_time_range_ns)
        - small_flow_pfc_count: PFC events during small flow active period
        - large_flow_pfc_count: ALL PFC events (for large flow analysis)
        - small_flow_time_range_ns: Time range covered by small flows
        - large_flow_time_range_ns: Total simulation time range
    """
    # Parse PFC file to get all event timestamps
    pfc_timestamps = []
    with open(pfc_path, 'r') as f:
        for line in f:
            if line.strip():
                parts = line.strip().split()
                if len(parts) >= 1:
                    try:
                        pfc_timestamps.append(int(parts[0]))
                    except ValueError:
                        pass

    if not pfc_timestamps:
        return 0, 0, 0, 0

    # Calculate total simulation time range from PFC events
    sim_start = min(pfc_timestamps) if pfc_timestamps else 0
    sim_end = max(pfc_timestamps) if pfc_timestamps else 0
    total_time_range = sim_end - sim_start

    # Get small flow time ranges if not provided
    if small_flow_timestamps is None and fct_path:
        small_flow_timestamps = []
        EXPERT_FLOW_SIZE = 8192  # 8KB
        LARGE_FLOW_SIZE = 8388608  # 8MB

        with open(fct_path, 'r') as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 7:
                    flow_size = int(parts[4])
                    flow_start_time = int(parts[5])
                    fct_ns = int(parts[6])

                    # Only small flows: store start and completion time
                    if flow_size == EXPERT_FLOW_SIZE:
                        completion_time = flow_start_time + fct_ns
                        small_flow_timestamps.append((flow_start_time, completion_time))

    # Count PFC for small flows: during the time range when small flows are active
    if small_flow_timestamps:
        # Find the overall time range when small flows are active
        small_flow_start = min(ts[0] for ts in small_flow_timestamps)
        small_flow_end = max(ts[1] for ts in small_flow_timestamps)
        small_flow_time_range = small_flow_end - small_flow_start

        # Count PFC events within small flow active time range
        small_pfc_count = sum(1 for ts in pfc_timestamps
                             if small_flow_start <= ts <= small_flow_end)
    else:
        small_pfc_count = 0
        small_flow_time_range = 0

    # Count PFC for large flows: ALL PFC events
    large_pfc_count = len(pfc_timestamps)

    return small_pfc_count, large_pfc_count, small_flow_time_range, total_time_range


def read_pfc_count_simple(pfc_path: str) -> int:
    """Simple PFC count: all events (legacy function for backward compatibility)."""
    count = 0
    with open(pfc_path, 'r') as f:
        for line in f:
            if line.strip():
                count += 1
    return count


def read_qlen_results(qlen_path: str) -> Tuple[float, float, float, float]:
    """Read queue length file and return avg, p50, p99, max in bytes."""
    qlen_values = []

    with open(qlen_path, 'r') as f:
        for line in f:
            parts = line.strip().split(',')
            if len(parts) >= 4:
                # Column 4 is queue length in bytes
                qlen = float(parts[3])
                qlen_values.append(qlen)

    if not qlen_values:
        return 0.0, 0.0, 0.0, 0.0

    qlen_values = np.array(qlen_values)
    avg = np.mean(qlen_values)
    max_val = np.max(qlen_values)

    p50, p99 = calculate_percentiles(qlen_values)

    return avg, p50, p99, max_val


def scan_output_directory(base_path: str) -> Dict[str, SimulationResult]:
    """Scan mix/output directory for simulation results."""
    results = {}

    base_dir = Path(base_path)
    if not base_dir.exists():
        print(f"Error: Directory {base_path} does not exist")
        return results

    for sim_dir in sorted(base_dir.iterdir()):
        if not sim_dir.is_dir():
            continue

        # Look for config.txt
        config_path = sim_dir / "config.txt"
        if not config_path.exists():
            continue

        try:
            lb_mode, fecmp_bg = parse_config(str(config_path))
        except (ValueError, IndexError) as e:
            print(f"Warning: Could not parse {config_path}: {e}")
            continue

        # Find output files (with pattern: {dir_id}_out_{type}.txt)
        dir_id = sim_dir.name
        fct_path = sim_dir / f"{dir_id}_out_fct.txt"
        pfc_path = sim_dir / f"{dir_id}_out_pfc.txt"
        qlen_path = sim_dir / f"{dir_id}_out_qlen.txt"

        if not fct_path.exists():
            # Try alternative naming
            for f in sim_dir.glob("*_out_fct.txt"):
                fct_path = f
                break

        if not pfc_path.exists():
            for f in sim_dir.glob("*_out_pfc.txt"):
                pfc_path = f
                break

        if not qlen_path.exists():
            for f in sim_dir.glob("*_out_qlen.txt"):
                qlen_path = f
                break

        # Create result object
        result = SimulationResult(lb_mode, fecmp_bg, str(sim_dir))

        # Read FCT data
        if fct_path.exists():
            (result.avg_fct, result.p50_fct, result.p99_fct, result.stddev_fct,
             result.total_timeout, result.avg_timeout, result.max_timeout,
             result.total_flows, result.large_avg_fct, result.large_p50_fct,
             result.large_p99_fct, result.large_total_flows, result.large_total_timeout,
             result.large_avg_timeout, result.large_max_timeout) = read_fct_results(str(fct_path))
        else:
            print(f"Warning: FCT file not found for {dir_id}")

        # Read PFC count with different methods for small and large flows
        if pfc_path.exists():
            if fct_path.exists():
                # Use FCT file to get small flow time range
                (result.pfc_count_small, result.pfc_count_large,
                 _, _) = read_pfc_count(str(pfc_path), str(fct_path))
                # Legacy: use large flow count for backward compatibility
                result.pfc_count = result.pfc_count_large
            else:
                # No FCT file, use simple count
                result.pfc_count = read_pfc_count_simple(str(pfc_path))
                result.pfc_count_small = 0
                result.pfc_count_large = result.pfc_count
        else:
            print(f"Warning: PFC file not found for {dir_id}")

        # Read queue length data
        if qlen_path.exists():
            result.avg_qlen, result.p50_qlen, result.p99_qlen, result.max_qlen = read_qlen_results(str(qlen_path))
        else:
            print(f"Warning: Qlen file not found for {dir_id}")

        key = result.mode_name
        results[key] = result

    return results


def format_number(val: float, decimals: int = 0) -> str:
    """Format number with specified decimals."""
    return f"{val:.{decimals}f}"


def format_delta(delta_percent: float, show_sign: bool = True) -> str:
    """Format percentage delta with color indicators."""
    if not show_sign:
        return f"{delta_percent:+.1f}%"

    sign = "+" if delta_percent > 0 else ""
    return f"{sign}{delta_percent:.1f}%"


def print_fct_table(results: Dict[str, SimulationResult]):
    """Print FCT performance comparison table."""
    # Sort order: Hybrid, MixHash, Drill, Hybrid-AS, Hybrid-SS, Inflex, then others (ECMP, Conweave)
    def sort_key(x):
        mode = x.split('(')[0]
        if mode == 'Hybrid':
            bg = int(x.split('(')[1].split(')')[0]) if '(' in x else 0
            return (0, bg)
        elif mode == 'MixHash':
            bg = int(x.split('(')[1].split(')')[0]) if '(' in x else 0
            return (1, bg)
        elif mode == 'Drill':
            bg = int(x.split('(')[1].split(')')[0]) if '(' in x else 0
            return (2, bg)
        elif mode == 'Hybrid-AS':
            bg = int(x.split('(')[1].split(')')[0]) if '(' in x else 0
            return (3, bg)
        elif mode == 'Hybrid-SS':
            bg = int(x.split('(')[1].split(')')[0]) if '(' in x else 0
            return (4, bg)
        elif mode == 'Inflex':
            bg = int(x.split('(')[1].split(')')[0]) if '(' in x else 0
            return (5, bg)
        elif mode == 'ECMP':
            return (6, 0)
        elif mode == 'Conweave':
            return (7, 0)  # No fecmp_bg for Conweave
        return (8, 0)

    sorted_keys = sorted(results.keys(), key=sort_key)

    # Find baseline: always use Hybrid(0) as baseline
    baseline = results.get("Hybrid(0)")
    baseline_avg = baseline.avg_fct if baseline else 0
    baseline_p50 = baseline.p50_fct if baseline else 0
    baseline_p99 = baseline.p99_fct if baseline else 0

    print("\n" + "="*135)
    print("FCT 性能对比 (Flow Completion Time) - 小流 (8KB专家流)")
    print("="*135)
    print(f"{'模式':<12} {'总流数':>10} {'Avg(μs)':>14} {'P50(μs)':>12} {'P99(μs)':>12} {'PFC(小)':>10} {'TotalTO':>12} {'AvgTO':>10} {'MaxTO':>8}")
    print("-"*135)

    for key in sorted_keys:
        r = results[key]

        # Calculate percentage deltas relative to baseline
        avg_delta = ((r.avg_fct - baseline_avg) / baseline_avg * 100) if baseline_avg > 0 else 0
        p50_delta = ((r.p50_fct - baseline_p50) / baseline_p50 * 100) if baseline_p50 > 0 else 0
        p99_delta = ((r.p99_fct - baseline_p99) / baseline_p99 * 100) if baseline_p99 > 0 else 0

        avg_str = f"{r.avg_fct:.2f} ({format_delta(avg_delta)})"
        p50_str = f"{r.p50_fct:.2f} ({format_delta(p50_delta)})"
        p99_str = f"{r.p99_fct:.2f} ({format_delta(p99_delta)})"

        pfc_str = f"{r.pfc_count_small:,}"  # Use small flow PFC count for small flow table
        timeout_str = f"{r.total_timeout:,}"
        avg_to_str = f"{r.avg_timeout:.2f}"
        max_to_str = f"{r.max_timeout}"

        print(f"{key:<12} {r.total_flows:>10} {avg_str:>14} {p50_str:>12} {p99_str:>12} {pfc_str:>10} {timeout_str:>12} {avg_to_str:>10} {max_to_str:>8}")

    print("="*135)


def print_large_flow_fct_table(results: Dict[str, SimulationResult]):
    """Print large flow FCT comparison table (8MB background flows)."""
    # Sort order: Hybrid, Hybrid-AS, Hybrid-SS, Inflex, then others (ECMP, Conweave)
    def sort_key(x):
        mode = x.split('(')[0]
        if mode == 'Hybrid':
            bg = int(x.split('(')[1].split(')')[0]) if '(' in x else 0
            return (0, bg)
        elif mode == 'Hybrid-AS':
            bg = int(x.split('(')[1].split(')')[0]) if '(' in x else 0
            return (1, bg)
        elif mode == 'Hybrid-SS':
            bg = int(x.split('(')[1].split(')')[0]) if '(' in x else 0
            return (2, bg)
        elif mode == 'Inflex':
            bg = int(x.split('(')[1].split(')')[0]) if '(' in x else 0
            return (3, bg)
        elif mode == 'ECMP':
            return (4, 0)
        elif mode == 'Conweave':
            return (5, 0)
        return (6, 0)

    sorted_keys = sorted(results.keys(), key=sort_key)

    # Find baseline: use Hybrid(0) if available, otherwise first with large flows
    baseline = None
    for key in sorted_keys:
        if results[key].large_total_flows > 0:
            baseline = results[key]
            break

    baseline_avg = baseline.large_avg_fct if baseline else 0
    baseline_p50 = baseline.large_p50_fct if baseline else 0
    baseline_p99 = baseline.large_p99_fct if baseline else 0

    print("\n" + "="*150)
    print("FCT 性能对比 - 大流 (8MB背景流)")
    print("="*150)
    print(f"{'模式':<12} {'大流数':>10} {'Avg(μs)':>14} {'P50(μs)':>12} {'P99(μs)':>12} {'PFC(全部)':>10} {'vs基线Avg':>12} {'TotalTO':>12} {'AvgTO':>10} {'MaxTO':>8}")
    print("-"*150)

    for key in sorted_keys:
        r = results[key]

        if r.large_total_flows == 0:
            continue  # Skip configurations with no large flows

        # Calculate percentage deltas relative to baseline
        if baseline_avg > 0:
            avg_delta = ((r.large_avg_fct - baseline_avg) / baseline_avg * 100)
            avg_str = f"{r.large_avg_fct:.2f} ({format_delta(avg_delta)})"
        else:
            avg_str = f"{r.large_avg_fct:.2f}"

        p50_str = f"{r.large_p50_fct:.2f}"
        p99_str = f"{r.large_p99_fct:.2f}"

        vs_baseline = f"{((r.large_avg_fct - baseline_avg) / baseline_avg * 100):+.1f}%" if baseline_avg > 0 else "N/A"

        large_pfc_str = f"{r.pfc_count_large:,}"
        large_to_str = f"{r.large_total_timeout:,}"
        large_avg_to_str = f"{r.large_avg_timeout:.2f}"
        large_max_to_str = f"{r.large_max_timeout}"

        print(f"{key:<12} {r.large_total_flows:>10} {avg_str:>14} {p50_str:>12} {p99_str:>12} {large_pfc_str:>10} {vs_baseline:>12} {large_to_str:>12} {large_avg_to_str:>10} {large_max_to_str:>8}")

    print("="*150)


def print_qlen_table(results: Dict[str, SimulationResult]):
    """Print queue length comparison table."""
    # Sort order: Hybrid, MixHash, Drill, Hybrid-AS, Hybrid-SS, Inflex, then others (ECMP, Conweave)
    def sort_key(x):
        mode = x.split('(')[0]
        if mode == 'Hybrid':
            bg = int(x.split('(')[1].split(')')[0]) if '(' in x else 0
            return (0, bg)
        elif mode == 'MixHash':
            bg = int(x.split('(')[1].split(')')[0]) if '(' in x else 0
            return (1, bg)
        elif mode == 'Drill':
            bg = int(x.split('(')[1].split(')')[0]) if '(' in x else 0
            return (2, bg)
        elif mode == 'Hybrid-AS':
            bg = int(x.split('(')[1].split(')')[0]) if '(' in x else 0
            return (3, bg)
        elif mode == 'Hybrid-SS':
            bg = int(x.split('(')[1].split(')')[0]) if '(' in x else 0
            return (4, bg)
        elif mode == 'Inflex':
            bg = int(x.split('(')[1].split(')')[0]) if '(' in x else 0
            return (5, bg)
        elif mode == 'ECMP':
            return (6, 0)
        elif mode == 'Conweave':
            return (7, 0)  # No fecmp_bg for Conweave
        return (8, 0)

    sorted_keys = sorted(results.keys(), key=sort_key)

    # Find Hybrid(0) baseline
    baseline = results.get("Hybrid(0)")
    baseline_avg = baseline.avg_qlen if baseline else 0
    baseline_p50 = baseline.p50_qlen if baseline else 0
    baseline_p99 = baseline.p99_qlen if baseline else 0
    baseline_max = baseline.max_qlen if baseline else 0

    print("\n" + "="*95)
    print("队列长度统计 (ToR上行链路)")
    print("="*95)
    print(f"{'模式':<12} {'Avg(字节)':>15} {'P50(字节)':>15} {'P99(字节)':>15} {'Max(字节)':>15}")
    print("-"*95)

    for key in sorted_keys:
        r = results[key]

        # Calculate percentage deltas
        avg_delta = ((r.avg_qlen - baseline_avg) / baseline_avg * 100) if baseline_avg > 0 else 0
        p50_delta = ((r.p50_qlen - baseline_p50) / baseline_p50 * 100) if baseline_p50 > 0 else 0
        p99_delta = ((r.p99_qlen - baseline_p99) / baseline_p99 * 100) if baseline_p99 > 0 else 0
        max_delta = ((r.max_qlen - baseline_max) / baseline_max * 100) if baseline_max > 0 else 0

        avg_str = f"{int(r.avg_qlen):,} ({format_delta(avg_delta)})"
        p50_str = f"{int(r.p50_qlen):,} ({format_delta(p50_delta)})"
        p99_str = f"{int(r.p99_qlen):,} ({format_delta(p99_delta)})"
        max_str = f"{int(r.max_qlen):,} ({format_delta(max_delta)})"

        print(f"{key:<12} {avg_str:>15} {p50_str:>15} {p99_str:>15} {max_str:>15}")

    print("="*95)


def print_summary(results: Dict[str, SimulationResult]):
    """Print summary analysis."""
    print("\n" + "="*100)
    print("关键发现 (Key Findings)")
    print("="*100)

    ecmp = results.get("ECMP")
    conweave = results.get("Conweave")
    hybrid_0 = results.get("Hybrid(0)")
    inflex_0 = results.get("Inflex(0)")
    hybrid_128 = results.get("Hybrid(128)")
    inflex_128 = results.get("Inflex(128)")
    hybrid_192 = results.get("Hybrid(192)")
    inflex_192 = results.get("Inflex(192)")

    # Helper function to safely calculate percentage difference
    def calc_pct_delta(new_val, base_val):
        if base_val > 0:
            return ((new_val - base_val) / base_val * 100)
        return None

    # Baseline: Hybrid(0) - pure MoE traffic without background flows
    if hybrid_0 and hybrid_0.avg_fct > 0:
        print(f"• 基准 Hybrid(0) Avg FCT: {hybrid_0.avg_fct:.2f}μs (纯MoE流量，无背景流)")

    # Comparison vs baseline at different FECMP_BG levels
    if hybrid_0 and conweave and conweave.avg_fct > 0:
        avg_delta = calc_pct_delta(conweave.avg_fct, hybrid_0.avg_fct)
        if avg_delta is not None:
            print(f"• Conweave vs Hybrid(0): {'优于' if avg_delta < 0 else '劣于'} {abs(avg_delta):.1f}%")

    # Low load - Inflex vs Hybrid(0)
    if hybrid_0 and inflex_0 and hybrid_0.avg_fct > 0:
        avg_delta = calc_pct_delta(inflex_0.avg_fct, hybrid_0.avg_fct)
        if avg_delta is not None:
            print(f"• Inflex(0) vs Hybrid(0): {'优于' if avg_delta < 0 else '劣于'} {abs(avg_delta):.1f}%")

    # Medium load (128) comparison
    if hybrid_128 and inflex_128 and hybrid_128.avg_fct > 0:
        avg_delta = calc_pct_delta(inflex_128.avg_fct, hybrid_128.avg_fct)
        if avg_delta is not None:
            print(f"• 中负载(128): Inflex Avg {'优于' if avg_delta < 0 else '劣于'} Hybrid {abs(avg_delta):.1f}%")
    # High load (192) comparison
    if hybrid_192 and inflex_192 and hybrid_192.avg_fct > 0:
        avg_delta = calc_pct_delta(inflex_192.avg_fct, hybrid_192.avg_fct)
        if avg_delta is not None:
            print(f"• 高负载(192): Inflex Avg {'优于' if avg_delta < 0 else '劣于'} Hybrid {abs(avg_delta):.1f}%")

    # Timeout statistics by mode
    print("\n--- 超时重传统计 ---")
    modes_to_compare = [("Hybrid", "Hybrid"), ("MixHash", "MixHash"), ("Drill", "Drill"),
                        ("Hybrid-AS", "Hybrid-AS"), ("Hybrid-SS", "Hybrid-SS"),
                        ("Conweave", "Conweave"), ("Inflex", "Inflex")]
    for mode_key, mode_name in modes_to_compare:
        total_to = sum(r.total_timeout for k, r in results.items() if mode_key in k)
        if total_to > 0:
            print(f"• {mode_name} 总超时: {total_to:,}")

    # Compare timeout ratios
    total_hybrid_to = sum(r.total_timeout for k, r in results.items() if "Hybrid" in k and "Hybrid-AS" not in k and "Hybrid-SS" not in k and "MixHash" not in k)
    total_mixhash_to = sum(r.total_timeout for k, r in results.items() if "MixHash" in k)
    total_drill_to = sum(r.total_timeout for k, r in results.items() if "Drill" in k)
    total_hybrid_as_to = sum(r.total_timeout for k, r in results.items() if "Hybrid-AS" in k)
    total_hybrid_ss_to = sum(r.total_timeout for k, r in results.items() if "Hybrid-SS" in k)
    total_conweave_to = sum(r.total_timeout for k, r in results.items() if "Conweave" in k)
    total_inflex_to = sum(r.total_timeout for k, r in results.items() if "Inflex" in k)

    if total_hybrid_to > 0 and total_mixhash_to > 0:
        to_ratio = total_mixhash_to / total_hybrid_to
        print(f"• MixHash vs Hybrid 超时比例: {to_ratio:.2f}x")
    if total_hybrid_to > 0 and total_drill_to > 0:
        to_ratio = total_drill_to / total_hybrid_to
        print(f"• Drill vs Hybrid 超时比例: {to_ratio:.2f}x")
    if total_hybrid_to > 0 and total_conweave_to > 0:
        to_ratio = total_conweave_to / total_hybrid_to
        print(f"• Conweave vs Hybrid 超时比例: {to_ratio:.2f}x")
    if total_hybrid_to > 0 and total_inflex_to > 0:
        to_ratio = total_inflex_to / total_hybrid_to
        print(f"• Inflex vs Hybrid 超时比例: {to_ratio:.2f}x")
    if total_hybrid_to > 0 and total_hybrid_as_to > 0:
        to_ratio = total_hybrid_as_to / total_hybrid_to
        print(f"• Hybrid-AS vs Hybrid 超时比例: {to_ratio:.2f}x")
    if total_hybrid_to > 0 and total_hybrid_ss_to > 0:
        to_ratio = total_hybrid_ss_to / total_hybrid_to
        print(f"• Hybrid-SS vs Hybrid 超时比例: {to_ratio:.2f}x")
    if total_conweave_to > 0 and total_inflex_to > 0:
        to_ratio = total_inflex_to / total_conweave_to
        print(f"• Inflex vs Conweave 超时比例: {to_ratio:.2f}x")

    # PFC comparison
    print("\n--- PFC事件统计 (小流:完成时统计, 大流:全部统计) ---")
    for mode_key, mode_name in modes_to_compare:
        total_pfc_small = sum(r.pfc_count_small for k, r in results.items() if mode_key in k)
        total_pfc_large = sum(r.pfc_count_large for k, r in results.items() if mode_key in k)
        if total_pfc_small > 0 or total_pfc_large > 0:
            print(f"• {mode_name} 小流PFC: {total_pfc_small:,}, 大流PFC(全部): {total_pfc_large:,}")

    total_hybrid_pfc_small = sum(r.pfc_count_small for k, r in results.items() if "Hybrid" in k and "Hybrid-AS" not in k and "Hybrid-SS" not in k and "MixHash" not in k)
    total_hybrid_pfc_large = sum(r.pfc_count_large for k, r in results.items() if "Hybrid" in k and "Hybrid-AS" not in k and "Hybrid-SS" not in k and "MixHash" not in k)
    total_mixhash_pfc_small = sum(r.pfc_count_small for k, r in results.items() if "MixHash" in k)
    total_mixhash_pfc_large = sum(r.pfc_count_large for k, r in results.items() if "MixHash" in k)
    total_drill_pfc_small = sum(r.pfc_count_small for k, r in results.items() if "Drill" in k)
    total_drill_pfc_large = sum(r.pfc_count_large for k, r in results.items() if "Drill" in k)
    total_hybrid_as_pfc_small = sum(r.pfc_count_small for k, r in results.items() if "Hybrid-AS" in k)
    total_hybrid_as_pfc_large = sum(r.pfc_count_large for k, r in results.items() if "Hybrid-AS" in k)
    total_hybrid_ss_pfc_small = sum(r.pfc_count_small for k, r in results.items() if "Hybrid-SS" in k)
    total_hybrid_ss_pfc_large = sum(r.pfc_count_large for k, r in results.items() if "Hybrid-SS" in k)
    total_conweave_pfc_small = sum(r.pfc_count_small for k, r in results.items() if "Conweave" in k)
    total_conweave_pfc_large = sum(r.pfc_count_large for k, r in results.items() if "Conweave" in k)
    total_inflex_pfc_small = sum(r.pfc_count_small for k, r in results.items() if "Inflex" in k)
    total_inflex_pfc_large = sum(r.pfc_count_large for k, r in results.items() if "Inflex" in k)

    if total_hybrid_pfc_small > 0 and total_mixhash_pfc_small > 0:
        pfc_ratio = total_mixhash_pfc_small / total_hybrid_pfc_small
        print(f"• MixHash vs Hybrid 小流PFC比例: {pfc_ratio:.2f}x")
    if total_hybrid_pfc_small > 0 and total_drill_pfc_small > 0:
        pfc_ratio = total_drill_pfc_small / total_hybrid_pfc_small
        print(f"• Drill vs Hybrid 小流PFC比例: {pfc_ratio:.2f}x")
    if total_hybrid_pfc_small > 0 and total_conweave_pfc_small > 0:
        pfc_ratio = total_conweave_pfc_small / total_hybrid_pfc_small
        print(f"• Conweave vs Hybrid 小流PFC比例: {pfc_ratio:.2f}x")
    if total_hybrid_pfc_small > 0 and total_inflex_pfc_small > 0:
        pfc_ratio = total_inflex_pfc_small / total_hybrid_pfc_small
        print(f"• Inflex vs Hybrid 小流PFC比例: {pfc_ratio:.2f}x")
    if total_hybrid_pfc_large > 0 and total_mixhash_pfc_large > 0:
        pfc_ratio = total_mixhash_pfc_large / total_hybrid_pfc_large
        print(f"• MixHash vs Hybrid 大流PFC比例: {pfc_ratio:.2f}x")
    if total_hybrid_pfc_large > 0 and total_drill_pfc_large > 0:
        pfc_ratio = total_drill_pfc_large / total_hybrid_pfc_large
        print(f"• Drill vs Hybrid 大流PFC比例: {pfc_ratio:.2f}x")
    if total_hybrid_pfc_large > 0 and total_conweave_pfc_large > 0:
        pfc_ratio = total_conweave_pfc_large / total_hybrid_pfc_large
        print(f"• Conweave vs Hybrid 大流PFC比例: {pfc_ratio:.2f}x")
    if total_hybrid_pfc_large > 0 and total_inflex_pfc_large > 0:
        pfc_ratio = total_inflex_pfc_large / total_hybrid_pfc_large
        print(f"• Inflex vs Hybrid 大流PFC比例: {pfc_ratio:.2f}x")
    if total_hybrid_pfc_small > 0 and total_hybrid_as_pfc_small > 0:
        pfc_ratio = total_hybrid_as_pfc_small / total_hybrid_pfc_small
        print(f"• Hybrid-AS vs Hybrid 小流PFC比例: {pfc_ratio:.2f}x")
    if total_hybrid_pfc_small > 0 and total_hybrid_ss_pfc_small > 0:
        pfc_ratio = total_hybrid_ss_pfc_small / total_hybrid_pfc_small
        print(f"• Hybrid-SS vs Hybrid 小流PFC比例: {pfc_ratio:.2f}x")
    if total_conweave_pfc_large > 0 and total_inflex_pfc_large > 0:
        pfc_ratio = total_inflex_pfc_large / total_conweave_pfc_large
        print(f"• Inflex vs Conweave 大流PFC比例: {pfc_ratio:.2f}x")

    print("="*100)
    print("="*70)


def main():
    """Main function."""
    # Default output directory
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "../mix/output")

    if len(sys.argv) > 1:
        output_dir = sys.argv[1]

    print(f"扫描目录: {output_dir}")

    results = scan_output_directory(output_dir)

    if not results:
        print("未找到有效的仿真结果")
        return

    print(f"\n找到 {len(results)} 个仿真结果:")
    for key in sorted(results.keys()):
        r = results[key]
        print(f"  {key}: LB_MODE={r.lb_mode}, FECMP_BG={r.fecmp_bg}")

    # Print tables
    print_fct_table(results)
    print_large_flow_fct_table(results)
    print_qlen_table(results)
    print_summary(results)


if __name__ == "__main__":
    main()
