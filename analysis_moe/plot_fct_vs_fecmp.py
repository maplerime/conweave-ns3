#!/usr/bin/env python3
import matplotlib.pyplot as plt
import numpy as np

# Baseline: Hybrid with fecmp_bg=0 (all drill): 163085 us
baseline_fct = 163085

# Data from analysis
modes = [
    'Hybrid\n(0 fecmp)',
    'Hybrid\n(64 fecmp)',
    'Hybrid\n(128 fecmp)',
    'Hybrid\n(192 fecmp)',
    'Pure\nFECMP',
    'Conweave'
]

avg_fct_us = [
    163085,    # Hybrid fecmp_bg=0
    368245,    # Hybrid fecmp_bg=64
    642009,    # Hybrid fecmp_bg=128
    1044714,   # Hybrid fecmp_bg=192
    174997,    # Pure FECMP
    179462     # Conweave
]

slowdown = [fct / baseline_fct for fct in avg_fct_us]
slowdown_pct = [(x - 1) * 100 for x in slowdown]

fig, ax = plt.subplots(figsize=(10, 5))

# Colors: green for baseline, shades of blue for intermediate hybrid, orange/red for high degradation, gray for other modes
colors = ['#2ecc71', '#3498db', '#9b59b6', '#e74c3c', '#95a5a6', '#f39c12']

bars = ax.bar(range(len(modes)), slowdown_pct, color=colors, edgecolor='black', linewidth=1.5)

for i, bar in enumerate(bars):
    height = bar.get_height()
    ax.text(bar.get_x() + bar.get_width()/2., height + 5,
            f'{slowdown[i]:.2f}x',
            ha='center', va='bottom', fontsize=9, fontweight='bold')

ax.set_xlabel('Load Balancing Mode', fontsize=12)
ax.set_ylabel('FCT Slowdown (%)', fontsize=12)
ax.set_title('8KB MoE Flow: FCT Slowdown Comparison\n(IRN-PFC, fat-k8-100G-OS10)', fontsize=13)
ax.set_xticks(range(len(modes)))
ax.set_xticklabels(modes, fontsize=10)
ax.axhline(y=0, color='gray', linestyle='--', linewidth=1, alpha=0.5)
ax.set_ylim(-10, max(slowdown_pct) * 1.15)
ax.grid(axis='y', alpha=0.3, linestyle='--')

# Add legend
from matplotlib.patches import Patch
legend_elements = [
    Patch(facecolor='#2ecc71', label='Baseline (all drill)'),
    Patch(facecolor='#3498db', label='Hybrid (some fecmp)'),
    Patch(facecolor='#e74c3c', label='Hybrid (all fecmp)'),
    Patch(facecolor='#95a5a6', label='Pure FECMP'),
    Patch(facecolor='#f39c12', label='Conweave')
]
ax.legend(handles=legend_elements, loc='upper left', fontsize=9)

plt.tight_layout()
output_file = "figures/8KB_SLOWDOWN_ALL_MODES.pdf"
plt.savefig(output_file, bbox_inches='tight')
print(f"Saved to {output_file}")
plt.close()
