#!/usr/bin/env python3
"""
NS-3 Simulation Results Comparison Tool

Compares FECMP, Letflow, Conga, Conweave, and NECMP load balancing modes.
Supports filtering flows by size range.
"""

import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import numpy as np


class SimulationResult:
    """Store simulation results for a single configuration."""

    def __init__(self, lb_mode: int, pfc: int, irn: int, path: str):
        self.lb_mode = lb_mode
        self.pfc = pfc  # 0 or 1
        self.irn = irn  # 0 or 1
        self.path = path

        # Map lb_mode to base name
        mode_names = {
            0: "FECMP",
            1: "ECMP",
            2: "DRILL",
            3: "Conga",
            6: "Letflow",
            9: "Conweave",
            15: "NECMP",
            16: "MixHash"
        }
        base_name = mode_names.get(lb_mode, f"Mode{lb_mode}")

        # Add flow control suffix
        if pfc == 1 and irn == 1:
            self.mode_name = f"{base_name}(PFC+IRN)"
        elif pfc == 1:
            self.mode_name = f"{base_name}(PFC)"
        elif irn == 1:
            self.mode_name = f"{base_name}(IRN)"
        else:
            self.mode_name = base_name

        # FCT metrics (in microseconds) - default all flows
        self.avg_fct = 0.0
        self.p50_fct = 0.0
        self.p90_fct = 0.0
        self.p99_fct = 0.0
        self.stddev_fct = 0.0

        # PFC count
        self.pfc_count = 0

        # Total flows count
        self.total_flows = 0

        # Retransmission (timeout) count
        self.total_retrans = 0


def parse_config(config_path: str) -> Tuple[int, int, int]:
    """Parse config.txt to extract LB_MODE, ENABLE_PFC, and ENABLE_IRN."""
    lb_mode = None
    pfc = None
    irn = None

    with open(config_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('LB_MODE'):
                lb_mode = int(line.split()[1])
            elif line.startswith('ENABLE_PFC'):
                pfc = int(line.split()[1])
            elif line.startswith('ENABLE_IRN'):
                irn = int(line.split()[1])

            if all(x is not None for x in [lb_mode, pfc, irn]):
                break

    return lb_mode, pfc, irn


def calculate_percentiles(data) -> Tuple[float, float, float]:
    """Calculate P50, P90, P99 percentiles."""
    if len(data) == 0:
        return 0.0, 0.0, 0.0
    sorted_data = np.sort(data)
    n = len(sorted_data)
    p50_idx = min(int(n * 0.5), n - 1)
    p90_idx = min(int(n * 0.9), n - 1)
    p99_idx = min(int(n * 0.99), n - 1)
    return float(sorted_data[p50_idx]), float(sorted_data[p90_idx]), float(sorted_data[p99_idx])


def read_fct_results(fct_path: str, size_min: int = 0, size_max: int = None) -> Tuple[float, float, float, float, float, int, int]:
    """Read FCT file and return avg, p50, p90, p99, stddev in microseconds, flow count, and total retransmissions.
    Filters flows by size range [size_min, size_max].
    """
    fct_values = []
    retrans_values = []

    with open(fct_path, 'r') as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 9:
                # Column 5 is flow size in bytes
                flow_size = int(parts[4])

                # Filter by size range
                if flow_size < size_min:
                    continue
                if size_max is not None and flow_size > size_max:
                    continue

                # Column 7 is FCT in nanoseconds
                fct_ns = float(parts[6])
                fct_values.append(fct_ns)

                # Column 9 is retransmission (timeout) count
                if len(parts) >= 9:
                    retrans_values.append(int(parts[8]))

    total_flows = len(fct_values)
    total_retrans = sum(retrans_values)

    if not fct_values:
        return 0.0, 0.0, 0.0, 0.0, 0.0, total_flows, total_retrans

    fct_values = np.array(fct_values)
    avg_us = np.mean(fct_values) / 1000  # Convert to microseconds
    stddev_us = np.std(fct_values) / 1000

    p50_us, p90_us, p99_us = calculate_percentiles(fct_values)
    p50_us /= 1000
    p90_us /= 1000
    p99_us /= 1000

    return avg_us, p50_us, p90_us, p99_us, stddev_us, total_flows, total_retrans


def read_pfc_count(pfc_path: str) -> int:
    """Count PFC events (number of lines in file)."""
    count = 0
    with open(pfc_path, 'r') as f:
        for line in f:
            if line.strip():
                count += 1
    return count


def scan_output_directory(base_path: str, size_min: int = 0, size_max: int = None) -> Dict[str, SimulationResult]:
    """Scan mix/output directory for simulation results.
    Returns a single dict with mode_name as key to support PFC, IRN, and PFC+IRN modes.
    Filters flows by size range [size_min, size_max].
    """
    results = {}

    base_dir = Path(base_path)
    if not base_dir.exists():
        print(f"Error: Directory {base_path} does not exist")
        return results

    # Target LB modes: FECMP(0), DRILL(2), Conga(3), Letflow(6), Conweave(9), NECMP(15), MixHash(16)
    target_modes = {0, 2, 3, 6, 9, 15, 16}

    for sim_dir in sorted(base_dir.iterdir()):
        if not sim_dir.is_dir():
            continue

        # Look for config.txt
        config_path = sim_dir / "config.txt"
        if not config_path.exists():
            continue

        try:
            lb_mode, pfc, irn = parse_config(str(config_path))
        except (ValueError, IndexError) as e:
            print(f"Warning: Could not parse {config_path}: {e}")
            continue

        # Only process target modes
        if lb_mode not in target_modes:
            continue

        # Create temp result to get mode_name
        temp_result = SimulationResult(lb_mode, pfc, irn, str(sim_dir))

        # Skip if we already have a result for this mode_name
        if temp_result.mode_name in results:
            continue

        # Find output files
        dir_id = sim_dir.name
        fct_path = sim_dir / f"{dir_id}_out_fct.txt"
        pfc_path = sim_dir / f"{dir_id}_out_pfc.txt"

        # Try alternative naming if not found
        if not fct_path.exists():
            for f in sim_dir.glob("*_out_fct.txt"):
                fct_path = f
                break

        if not pfc_path.exists():
            for f in sim_dir.glob("*_out_pfc.txt"):
                pfc_path = f
                break

        # Create result object
        result = SimulationResult(lb_mode, pfc, irn, str(sim_dir))

        # Read FCT data with size filter
        if fct_path.exists():
            (result.avg_fct, result.p50_fct, result.p90_fct, result.p99_fct,
             result.stddev_fct, result.total_flows, result.total_retrans) = read_fct_results(str(fct_path), size_min, size_max)
        else:
            print(f"Warning: FCT file not found for {dir_id}")

        # Read PFC count
        if pfc_path.exists():
            result.pfc_count = read_pfc_count(str(pfc_path))

        results[result.mode_name] = result
        fc_str = "PFC+IRN" if (pfc == 1 and irn == 1) else ("PFC" if pfc == 1 else ("IRN" if irn == 1 else "None"))
        print(f"Found: {result.mode_name} (LB_MODE={lb_mode}, {fc_str}) in {dir_id}")

    return results


def format_value_with_delta(val: float, baseline: float, decimals: int = 2) -> str:
    """Format value with percentage delta relative to baseline."""
    return f"{val:.{decimals}f}"


def format_delta(val: float, baseline: float) -> str:
    """Format percentage delta relative to baseline."""
    if baseline > 0:
        delta = ((val - baseline) / baseline * 100)
        sign = "+" if delta > 0 else ""
        return f"{sign}{delta:.1f}%"
    return "-"


def format_size_range(size_min: int, size_max: Optional[int]) -> str:
    """Format size range for display."""
    if size_max is None:
        if size_min == 0:
            return "所有流"
        return f">={size_min//1024}KB"
    if size_max >= 1000000:
        return f"{size_min//1024}KB-{size_max//1024//1024}MB"
    return f"{size_min//1024}KB-{size_max//1024}KB"


def print_fct_table(results: Dict[str, SimulationResult], section_title: str, size_min: int, size_max: Optional[int]):
    """Print FCT performance comparison table."""
    # Define order for each flow control mode (added DRILL and MixHash)
    pfc_order = ["FECMP", "DRILL", "Conga", "Letflow", "Conweave", "NECMP", "MixHash"]
    irn_order = ["FECMP", "DRILL", "Conga", "Letflow", "Conweave", "NECMP", "MixHash"]
    pfc_irn_order = ["FECMP", "DRILL", "Conga", "Letflow", "Conweave", "NECMP", "MixHash"]

    size_range_str = format_size_range(size_min, size_max)

    # Section 1: PFC (IRN=0, PFC=1)
    has_pfc = any(f"{mode}(PFC)" in results for mode in pfc_order)
    if has_pfc:
        baseline = None
        for mode in pfc_order:
            key = f"{mode}(PFC)"
            if key in results:
                baseline = results[key]
                break

        if baseline:
            baseline_avg = baseline.avg_fct
            baseline_p50 = baseline.p50_fct
            baseline_p90 = baseline.p90_fct
            baseline_p99 = baseline.p99_fct

            print("\n" + "="*150)
            print(f"FCT Performance Comparison (PFC=1, IRN=0) - {size_range_str}")
            print("="*150)
            print(f"{'Mode':<12} {'Flows':>10} {'Avg(us)':>12} {'Avg(Δ%)':>12} {'P99(us)':>12} {'P99(Δ%)':>12} {'PFC':>10} {'ReTx':>10}")
            print("-"*150)

            for mode in pfc_order:
                key = f"{mode}(PFC)"
                if key not in results:
                    continue
                r = results[key]
                avg_val = format_value_with_delta(r.avg_fct, baseline_avg)
                avg_delta = format_delta(r.avg_fct, baseline_avg)
                p99_val = format_value_with_delta(r.p99_fct, baseline_p99)
                p99_delta = format_delta(r.p99_fct, baseline_p99)
                print(f"{mode:<12} {r.total_flows:>10} {avg_val:>12} {avg_delta:>12} {p99_val:>12} {p99_delta:>12} {r.pfc_count:>10} {r.total_retrans:>10}")

            print("="*150)

    # Section 2: IRN (IRN=1, PFC=0)
    has_irn = any(f"{mode}(IRN)" in results for mode in irn_order)
    if has_irn:
        baseline = None
        for mode in irn_order:
            key = f"{mode}(IRN)"
            if key in results:
                baseline = results[key]
                break

        if baseline:
            baseline_avg = baseline.avg_fct
            baseline_p50 = baseline.p50_fct
            baseline_p90 = baseline.p90_fct
            baseline_p99 = baseline.p99_fct

            print("\n" + "="*150)
            print(f"FCT Performance Comparison (IRN=1, PFC=0) - {size_range_str}")
            print("="*150)
            print(f"{'Mode':<12} {'Flows':>10} {'Avg(us)':>12} {'Avg(Δ%)':>12} {'P99(us)':>12} {'P99(Δ%)':>12} {'PFC':>10} {'ReTx':>10}")
            print("-"*150)

            for mode in irn_order:
                key = f"{mode}(IRN)"
                if key not in results:
                    continue
                r = results[key]
                avg_val = format_value_with_delta(r.avg_fct, baseline_avg)
                avg_delta = format_delta(r.avg_fct, baseline_avg)
                p99_val = format_value_with_delta(r.p99_fct, baseline_p99)
                p99_delta = format_delta(r.p99_fct, baseline_p99)
                print(f"{mode:<12} {r.total_flows:>10} {avg_val:>12} {avg_delta:>12} {p99_val:>12} {p99_delta:>12} {r.pfc_count:>10} {r.total_retrans:>10}")

            print("="*150)

    # Section 3: PFC+IRN (IRN=1, PFC=1)
    has_pfc_irn = any(f"{mode}(PFC+IRN)" in results for mode in pfc_irn_order)
    if has_pfc_irn:
        baseline = None
        for mode in pfc_irn_order:
            key = f"{mode}(PFC+IRN)"
            if key in results:
                baseline = results[key]
                break

        if baseline:
            baseline_avg = baseline.avg_fct
            baseline_p50 = baseline.p50_fct
            baseline_p90 = baseline.p90_fct
            baseline_p99 = baseline.p99_fct

            print("\n" + "="*150)
            print(f"FCT Performance Comparison (PFC=1, IRN=1) - {size_range_str}")
            print("="*150)
            print(f"{'Mode':<12} {'Flows':>10} {'Avg(us)':>12} {'Avg(Δ%)':>12} {'P99(us)':>12} {'P99(Δ%)':>12} {'PFC':>10} {'ReTx':>10}")
            print("-"*150)

            for mode in pfc_irn_order:
                key = f"{mode}(PFC+IRN)"
                if key not in results:
                    continue
                r = results[key]
                avg_val = format_value_with_delta(r.avg_fct, baseline_avg)
                avg_delta = format_delta(r.avg_fct, baseline_avg)
                p99_val = format_value_with_delta(r.p99_fct, baseline_p99)
                p99_delta = format_delta(r.p99_fct, baseline_p99)
                print(f"{mode:<12} {r.total_flows:>10} {avg_val:>12} {avg_delta:>12} {p99_val:>12} {p99_delta:>12} {r.pfc_count:>10} {r.total_retrans:>10}")

            print("="*150)


def print_summary(results: Dict[str, SimulationResult], section_title: str):
    """Print summary analysis."""
    def calc_pct_delta(new_val, base_val):
        if base_val > 0:
            return ((new_val - base_val) / base_val * 100)
        return None

    base_modes = ["FECMP", "DRILL", "Conga", "Letflow", "Conweave", "NECMP", "MixHash"]

    # Section 1: PFC (IRN=0, PFC=1)
    has_pfc = any(f"{mode}(PFC)" in results for mode in base_modes)
    if has_pfc:
        print("\n" + "="*100)
        print(f"关键发现 (PFC=1, IRN=0) - {section_title}")
        print("="*100)

        for base_mode in base_modes:
            key = f"{base_mode}(PFC)"
            if key in results:
                r = results[key]
                print(f"• {base_mode} Avg FCT: {r.avg_fct:.2f}μs, PFC: {r.pfc_count:,}, 重传: {r.total_retrans:,}")

    # Section 2: IRN (IRN=1, PFC=0)
    has_irn = any(f"{mode}(IRN)" in results for mode in base_modes)
    if has_irn:
        print("\n" + "="*100)
        print(f"关键发现 (IRN=1, PFC=0) - {section_title}")
        print("="*100)

        for base_mode in base_modes:
            key = f"{base_mode}(IRN)"
            if key in results:
                r = results[key]
                print(f"• {base_mode} Avg FCT: {r.avg_fct:.2f}μs, PFC: {r.pfc_count:,}, 重传: {r.total_retrans:,}")

    # Section 3: PFC+IRN (IRN=1, PFC=1)
    has_pfc_irn = any(f"{mode}(PFC+IRN)" in results for mode in base_modes)
    if has_pfc_irn:
        print("\n" + "="*100)
        print(f"关键发现 (PFC=1, IRN=1) - {section_title}")
        print("="*100)

        for base_mode in base_modes:
            key = f"{base_mode}(PFC+IRN)"
            if key in results:
                r = results[key]
                print(f"• {base_mode} Avg FCT: {r.avg_fct:.2f}μs, PFC: {r.pfc_count:,}, 重传: {r.total_retrans:,}")

    # Cross-mode comparison
    if has_pfc and has_irn and has_pfc_irn:
        print("\n" + "="*100)
        print("流控模式对比 (PFC vs IRN vs PFC+IRN)")
        print("="*100)

        for base_mode in base_modes:
            pfc_only = results.get(f"{base_mode}(PFC)")
            irn_only = results.get(f"{base_mode}(IRN)")
            pfc_irn = results.get(f"{base_mode}(PFC+IRN)")

            if pfc_only or irn_only or pfc_irn:
                print(f"\n{base_mode}:")

                if pfc_only and irn_only:
                    delta = calc_pct_delta(irn_only.avg_fct, pfc_only.avg_fct)
                    if delta is not None:
                        print(f"  IRN vs PFC: {'优于' if delta < 0 else '劣于'} {abs(delta):.1f}%")

                if pfc_only and pfc_irn:
                    delta = calc_pct_delta(pfc_irn.avg_fct, pfc_only.avg_fct)
                    if delta is not None:
                        print(f"  PFC+IRN vs PFC: {'优于' if delta < 0 else '劣于'} {abs(delta):.1f}%")

                if irn_only and pfc_irn:
                    delta = calc_pct_delta(pfc_irn.avg_fct, irn_only.avg_fct)
                    if delta is not None:
                        print(f"  PFC+IRN vs IRN: {'优于' if delta < 0 else '劣于'} {abs(delta):.1f}%")

    print("="*100)


def main():
    """Main function."""
    import argparse

    parser = argparse.ArgumentParser(description='NS-3 Simulation Results Comparison')
    parser.add_argument('--output-dir', '-o', default=None, help='Output directory path')
    parser.add_argument('--size-min', '-min', type=int, default=0, help='Minimum flow size in bytes')
    parser.add_argument('--size-max', '-max', type=int, default=None, help='Maximum flow size in bytes')

    args = parser.parse_args()

    # Default output directory
    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../mix/output")

    size_min = args.size_min
    size_max = args.size_max

    size_range_str = format_size_range(size_min, size_max)
    print(f"扫描目录: {output_dir}")
    print(f"流大小范围: {size_range_str}")
    print(f"目标模式: FECMP(0), DRILL(2), Conga(3), Letflow(6), Conweave(9), NECMP(15), MixHash(16)")
    print(f"支持流控模式: PFC, IRN, PFC+IRN")

    results = scan_output_directory(output_dir, size_min, size_max)

    if not results:
        print("未找到有效的仿真结果")
        return

    print(f"\n找到 {len(results)} 个仿真结果")

    # Print tables
    print_fct_table(results, "所有模式", size_min, size_max)
    print_summary(results, "流控模式对比")


if __name__ == "__main__":
    main()
