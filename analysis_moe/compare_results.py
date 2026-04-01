#!/usr/bin/env python3
"""
NS-3 Simulation Results Comparison Tool

Compares Hybrid vs Inflex load balancing modes across different FECMP_BG ratios.
Generates FCT performance tables and queue length statistics with percentage
changes relative to Hybrid(0) baseline.
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
        self.lb_mode = lb_mode  # 10 = Hybrid, 12 = Inflex
        self.fecmp_bg = fecmp_bg
        self.path = path
        self.mode_name = f"Hybrid({fecmp_bg})" if lb_mode == 10 else f"Inflex({fecmp_bg})"

        # FCT metrics (in microseconds)
        self.avg_fct = 0.0
        self.p50_fct = 0.0
        self.p99_fct = 0.0
        self.stddev_fct = 0.0

        # PFC count
        self.pfc_count = 0

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


def read_fct_results(fct_path: str) -> Tuple[float, float, float, float]:
    """Read FCT file and return avg, p50, p99, stddev in microseconds."""
    fct_values = []

    with open(fct_path, 'r') as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 7:
                # Column 7 is FCT in nanoseconds
                fct_ns = float(parts[6])
                fct_values.append(fct_ns)

    if not fct_values:
        return 0.0, 0.0, 0.0, 0.0

    fct_values = np.array(fct_values)
    avg_us = np.mean(fct_values) / 1000  # Convert to microseconds
    stddev_us = np.std(fct_values) / 1000

    p50_us, p99_us = calculate_percentiles(fct_values)
    p50_us /= 1000
    p99_us /= 1000

    return avg_us, p50_us, p99_us, stddev_us


def read_pfc_count(pfc_path: str) -> int:
    """Count PFC events (number of lines in file)."""
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
            result.avg_fct, result.p50_fct, result.p99_fct, result.stddev_fct = read_fct_results(str(fct_path))
        else:
            print(f"Warning: FCT file not found for {dir_id}")

        # Read PFC count
        if pfc_path.exists():
            result.pfc_count = read_pfc_count(str(pfc_path))
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
    # Sort by mode name (Hybrid first, then Inflex, by fBG value)
    sorted_keys = sorted(results.keys(), key=lambda x: (x.split('(')[0], int(x.split('(')[1].split(')')[0])))

    # Find Hybrid(0) baseline
    baseline = results.get("Hybrid(0)")
    baseline_avg = baseline.avg_fct if baseline else 0
    baseline_p50 = baseline.p50_fct if baseline else 0
    baseline_p99 = baseline.p99_fct if baseline else 0

    print("\n" + "="*90)
    print("FCT 性能对比 (Flow Completion Time)")
    print("="*90)
    print(f"{'模式':<12} {'Avg(μs)':>12} {'P50(μs)':>12} {'P99(μs)':>12} {'PFC(万)':>12} {'StdDev':>12}")
    print("-"*90)

    for key in sorted_keys:
        r = results[key]

        # Calculate percentage deltas relative to Hybrid(0)
        avg_delta = ((r.avg_fct - baseline_avg) / baseline_avg * 100) if baseline_avg > 0 else 0
        p50_delta = ((r.p50_fct - baseline_p50) / baseline_p50 * 100) if baseline_p50 > 0 else 0
        p99_delta = ((r.p99_fct - baseline_p99) / baseline_p99 * 100) if baseline_p99 > 0 else 0

        avg_str = f"{int(r.avg_fct)} ({format_delta(avg_delta)})"
        p50_str = f"{int(r.p50_fct)} ({format_delta(p50_delta)})"
        p99_str = f"{int(r.p99_fct)} ({format_delta(p99_delta)})"

        pfc_str = f"{r.pfc_count / 10000:.1f}"
        stddev_str = f"{int(r.stddev_fct)}"

        print(f"{key:<12} {avg_str:>12} {p50_str:>12} {p99_str:>12} {pfc_str:>12} {stddev_str:>12}")

    print("="*90)


def print_qlen_table(results: Dict[str, SimulationResult]):
    """Print queue length comparison table."""
    sorted_keys = sorted(results.keys(), key=lambda x: (x.split('(')[0], int(x.split('(')[1].split(')')[0])))

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
    print("\n" + "="*70)
    print("关键发现 (Key Findings)")
    print("="*70)

    hybrid_0 = results.get("Hybrid(0)")
    inflex_0 = results.get("Inflex(0)")
    hybrid_192 = results.get("Hybrid(192)")
    inflex_192 = results.get("Inflex(192)")

    if hybrid_0 and inflex_0:
        avg_delta = ((inflex_0.avg_fct - hybrid_0.avg_fct) / hybrid_0.avg_fct * 100)
        p50_delta = ((inflex_0.p50_fct - hybrid_0.p50_fct) / hybrid_0.p50_fct * 100)
        print(f"• 低负载(0): Inflex Avg {'优于' if avg_delta < 0 else '劣于'} Hybrid {abs(avg_delta):.1f}%")
        print(f"• 低负载(0): Inflex P50 {'优于' if p50_delta < 0 else '劣于'} Hybrid {abs(p50_delta):.1f}%")

    if hybrid_192 and inflex_192:
        avg_delta = ((inflex_192.avg_fct - hybrid_192.avg_fct) / hybrid_192.avg_fct * 100)
        p99_delta = ((inflex_192.p99_fct - hybrid_192.p99_fct) / hybrid_192.p99_fct * 100)
        print(f"• 高负载(192): Inflex Avg {'优于' if avg_delta < 0 else '劣于'} Hybrid {abs(avg_delta):.1f}%")
        print(f"• 高负载(192): Inflex P99 {'优于' if p99_delta < 0 else '劣于'} Hybrid {abs(p99_delta):.1f}%")

    # PFC comparison
    total_hybrid_pfc = sum(r.pfc_count for k, r in results.items() if "Hybrid" in k)
    total_inflex_pfc = sum(r.pfc_count for k, r in results.items() if "Inflex" in k)
    if total_hybrid_pfc > 0:
        pfc_ratio = total_inflex_pfc / total_hybrid_pfc
        print(f"• PFC事件: Inflex 是 Hybrid 的 {pfc_ratio:.2f}x")

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
    print_qlen_table(results)
    print_summary(results)


if __name__ == "__main__":
    main()
