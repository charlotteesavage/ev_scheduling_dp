"""
Visualization script to compare DP and CPLEX results.

Creates side-by-side visualizations of:
1. Activity timelines (Gantt chart)
2. SOC trajectories over 24 hours
3. Charging session comparisons
4. Key metrics comparison
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pathlib import Path
import sys


def load_schedules(parent_dir):
    """Load both DP and CPLEX schedules if available."""
    schedules = {}

    # DP schedule
    dp_file = parent_dir / "dylan_data" / "dylan_optimal_schedule_dp.csv"
    if dp_file.exists():
        schedules['dp'] = pd.read_csv(dp_file)
        print(f"✓ Loaded DP schedule: {len(schedules['dp'])} activities")
    else:
        print(f"✗ DP schedule not found: {dp_file}")

    # CPLEX schedule (would come from running solution_analysis.ipynb)
    cplex_file = parent_dir / "dylan_data" / "dylan_optimal_schedule_cplex.csv"
    if cplex_file.exists():
        schedules['cplex'] = pd.read_csv(cplex_file)
        print(f"✓ Loaded CPLEX schedule: {len(schedules['cplex'])} activities")
    else:
        print(f"✗ CPLEX schedule not found (run solution_analysis.ipynb to generate)")

    return schedules


def plot_gantt_chart(schedules, output_file):
    """Create Gantt chart comparing schedules."""
    n_schedules = len(schedules)

    fig, axes = plt.subplots(n_schedules, 1, figsize=(14, 4*n_schedules), sharex=True)
    if n_schedules == 1:
        axes = [axes]

    # Color mapping for activities
    colors = {
        'home': '#90EE90',
        'dawn': '#90EE90',
        'dusk': '#90EE90',
        'work': '#FFB6C1',
        'education': '#ADD8E6',
        'shopping': '#FFD700',
        'leisure': '#DDA0DD',
        'escort': '#FFA07A',
        'errands': '#F0E68C',
        'service': '#FF6347',
        'default': '#D3D3D3',
    }

    for idx, (name, schedule) in enumerate(schedules.items()):
        ax = axes[idx]

        for _, row in schedule.iterrows():
            start = row['start_time']
            duration = row['duration']
            act_type = str(row['act_type']).lower()

            # Determine color
            color = colors.get('default')
            for key in colors:
                if key in act_type:
                    color = colors[key]
                    break

            # Add charging indicator
            if row['is_charging']:
                edgecolor = 'red'
                linewidth = 3
            else:
                edgecolor = 'black'
                linewidth = 1

            # Draw bar
            ax.barh(
                y=0,
                width=duration,
                left=start,
                height=0.8,
                color=color,
                edgecolor=edgecolor,
                linewidth=linewidth,
                label=act_type
            )

            # Add text label
            if duration > 0.5:  # Only label if wide enough
                ax.text(
                    start + duration/2,
                    0,
                    act_type.split()[0][:8],
                    ha='center',
                    va='center',
                    fontsize=8
                )

        ax.set_xlim(0, 24)
        ax.set_ylim(-0.5, 0.5)
        ax.set_yticks([])
        ax.set_ylabel(name.upper(), fontsize=12, fontweight='bold')
        ax.grid(axis='x', alpha=0.3)
        ax.axvline(x=12, color='gray', linestyle='--', alpha=0.5, linewidth=0.5)

    axes[-1].set_xlabel('Time of Day (hours)', fontsize=12)
    plt.suptitle('Activity Schedule Comparison', fontsize=14, fontweight='bold')

    # Add legend
    legend_elements = [
        mpatches.Patch(color=colors['home'], label='Home'),
        mpatches.Patch(color=colors.get('work', '#FFB6C1'), label='Work'),
        mpatches.Patch(color=colors.get('leisure', '#DDA0DD'), label='Leisure'),
        mpatches.Patch(color=colors.get('escort', '#FFA07A'), label='Escort'),
        mpatches.Patch(color=colors.get('service', '#FF6347'), label='Service Station'),
        mpatches.Rectangle((0,0),1,1, facecolor='white', edgecolor='red', linewidth=3, label='Charging'),
    ]
    axes[0].legend(handles=legend_elements, loc='upper right', fontsize=9)

    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ Saved Gantt chart: {output_file}")
    plt.close()


def plot_soc_trajectory(schedules, output_file):
    """Plot SOC trajectories over 24 hours."""
    fig, ax = plt.subplots(figsize=(14, 6))

    colors_map = {
        'dp': 'blue',
        'cplex': 'green',
    }

    for name, schedule in schedules.items():
        # Create SOC trajectory
        times = [0]
        socs = [schedule['soc_start'].iloc[0]]

        for _, row in schedule.iterrows():
            # SOC at start of activity
            times.append(row['start_time'])
            socs.append(row['soc_start'])

            # SOC at end of activity
            times.append(row['start_time'] + row['duration'])
            socs.append(row['soc_end'])

        # Plot
        ax.plot(times, socs, label=name.upper(), linewidth=2,
                color=colors_map.get(name, 'gray'), marker='o', markersize=4)

        # Mark charging sessions
        charging_acts = schedule[schedule['is_charging'] == 1]
        for _, row in charging_acts.iterrows():
            ax.axvspan(
                row['start_time'],
                row['start_time'] + row['charge_duration'],
                alpha=0.2,
                color='red'
            )

    # Add threshold line
    ax.axhline(y=0.3, color='red', linestyle='--', linewidth=1.5,
               label='30% Threshold', alpha=0.7)

    ax.set_xlabel('Time of Day (hours)', fontsize=12)
    ax.set_ylabel('State of Charge (SOC)', fontsize=12)
    ax.set_title('Battery SOC Trajectory Comparison', fontsize=14, fontweight='bold')
    ax.set_xlim(0, 24)
    ax.set_ylim(0, 1.05)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=10)

    # Format y-axis as percentage
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f'{y:.0%}'))

    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ Saved SOC trajectory: {output_file}")
    plt.close()


def plot_charging_comparison(schedules, output_file):
    """Compare charging sessions between approaches."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    # Count charging sessions by type
    charging_data = {}
    for name, schedule in schedules.items():
        charging_acts = schedule[schedule['is_charging'] == 1]
        if 'charge_mode' in schedule.columns:
            slow = (charging_acts['charge_mode'] == 1).sum()
            fast = (charging_acts['charge_mode'] == 2).sum()
            rapid = (charging_acts['charge_mode'] == 3).sum()
        else:
            # Try to infer from other columns
            slow = fast = rapid = 0
            if len(charging_acts) > 0:
                rapid = len(charging_acts)  # Assume rapid if not specified

        charging_data[name] = {
            'Slow (7kW)': slow,
            'Fast (22kW)': fast,
            'Rapid (50kW)': rapid,
            'Total Time': charging_acts['charge_duration'].sum() if 'charge_duration' in charging_acts else 0,
        }

    # Plot 1: Charging sessions by type
    ax1 = axes[0]
    x = np.arange(len(schedules))
    width = 0.25

    for i, charge_type in enumerate(['Slow (7kW)', 'Fast (22kW)', 'Rapid (50kW)']):
        values = [charging_data[name][charge_type] for name in schedules.keys()]
        ax1.bar(x + i*width, values, width, label=charge_type)

    ax1.set_ylabel('Number of Sessions', fontsize=11)
    ax1.set_title('Charging Sessions by Type', fontsize=12, fontweight='bold')
    ax1.set_xticks(x + width)
    ax1.set_xticklabels([name.upper() for name in schedules.keys()])
    ax1.legend(fontsize=9)
    ax1.grid(axis='y', alpha=0.3)

    # Plot 2: Total charging time
    ax2 = axes[1]
    names = list(schedules.keys())
    times = [charging_data[name]['Total Time'] for name in names]
    colors = ['blue' if name == 'dp' else 'green' for name in names]

    bars = ax2.bar(range(len(names)), times, color=colors, alpha=0.7)
    ax2.set_ylabel('Total Charging Time (hours)', fontsize=11)
    ax2.set_title('Total Charging Duration', fontsize=12, fontweight='bold')
    ax2.set_xticks(range(len(names)))
    ax2.set_xticklabels([name.upper() for name in names])
    ax2.grid(axis='y', alpha=0.3)

    # Add value labels on bars
    for bar in bars:
        height = bar.get_height()
        ax2.text(bar.get_x() + bar.get_width()/2., height,
                f'{height:.2f}h',
                ha='center', va='bottom', fontsize=10)

    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ Saved charging comparison: {output_file}")
    plt.close()


def plot_metrics_comparison(schedules, output_file):
    """Create comprehensive metrics comparison."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    metrics = {}
    for name, schedule in schedules.items():
        metrics[name] = {
            'Activities': len(schedule),
            'Initial SOC': schedule['soc_start'].iloc[0],
            'Final SOC': schedule['soc_end'].iloc[-1],
            'Min SOC': schedule['soc_start'].min(),
            'Charging Sessions': int(schedule['is_charging'].sum()),
            'Total Charging Time': schedule[schedule['is_charging']==1]['charge_duration'].sum() if 'charge_duration' in schedule else 0,
        }

    # Plot 1: SOC metrics
    ax1 = axes[0, 0]
    x = np.arange(len(schedules))
    width = 0.25

    soc_metrics = ['Initial SOC', 'Final SOC', 'Min SOC']
    for i, metric in enumerate(soc_metrics):
        values = [metrics[name][metric] for name in schedules.keys()]
        ax1.bar(x + i*width, values, width, label=metric)

    ax1.set_ylabel('State of Charge', fontsize=11)
    ax1.set_title('SOC Metrics Comparison', fontsize=12, fontweight='bold')
    ax1.set_xticks(x + width)
    ax1.set_xticklabels([name.upper() for name in schedules.keys()])
    ax1.legend(fontsize=9)
    ax1.axhline(y=0.3, color='red', linestyle='--', alpha=0.5, label='Threshold')
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f'{y:.0%}'))
    ax1.grid(axis='y', alpha=0.3)

    # Plot 2: Activity count
    ax2 = axes[0, 1]
    names = list(schedules.keys())
    counts = [metrics[name]['Activities'] for name in names]
    colors = ['blue' if name == 'dp' else 'green' for name in names]

    bars = ax2.bar(range(len(names)), counts, color=colors, alpha=0.7)
    ax2.set_ylabel('Number of Activities', fontsize=11)
    ax2.set_title('Total Activities Scheduled', fontsize=12, fontweight='bold')
    ax2.set_xticks(range(len(names)))
    ax2.set_xticklabels([name.upper() for name in names])
    ax2.grid(axis='y', alpha=0.3)

    for bar in bars:
        height = bar.get_height()
        ax2.text(bar.get_x() + bar.get_width()/2., height,
                f'{int(height)}',
                ha='center', va='bottom', fontsize=11)

    # Plot 3: Charging sessions
    ax3 = axes[1, 0]
    sessions = [metrics[name]['Charging Sessions'] for name in names]

    bars = ax3.bar(range(len(names)), sessions, color=colors, alpha=0.7)
    ax3.set_ylabel('Number of Sessions', fontsize=11)
    ax3.set_title('Charging Sessions', fontsize=12, fontweight='bold')
    ax3.set_xticks(range(len(names)))
    ax3.set_xticklabels([name.upper() for name in names])
    ax3.grid(axis='y', alpha=0.3)

    for bar in bars:
        height = bar.get_height()
        ax3.text(bar.get_x() + bar.get_width()/2., height,
                f'{int(height)}',
                ha='center', va='bottom', fontsize=11)

    # Plot 4: Summary table
    ax4 = axes[1, 1]
    ax4.axis('tight')
    ax4.axis('off')

    table_data = []
    row_labels = ['Activities', 'Initial SOC', 'Final SOC', 'Min SOC',
                  'Charging Sessions', 'Total Charge Time']

    for label in row_labels:
        row = [label]
        for name in schedules.keys():
            if 'SOC' in label:
                value = f"{metrics[name][label]:.1%}"
            elif 'Time' in label:
                value = f"{metrics[name][label]:.2f}h"
            else:
                value = f"{metrics[name][label]}"
            row.append(value)
        table_data.append(row)

    col_labels = ['Metric'] + [name.upper() for name in schedules.keys()]
    table = ax4.table(cellText=table_data, colLabels=col_labels,
                     cellLoc='center', loc='center',
                     colWidths=[0.4] + [0.3]*len(schedules))

    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 2)

    # Color header
    for i in range(len(col_labels)):
        table[(0, i)].set_facecolor('#4472C4')
        table[(0, i)].set_text_props(weight='bold', color='white')

    ax4.set_title('Metrics Summary', fontsize=12, fontweight='bold', pad=20)

    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ Saved metrics comparison: {output_file}")
    plt.close()


def main():
    """Main execution function."""
    print("="*70)
    print("VISUALIZING DP vs CPLEX COMPARISON")
    print("="*70)

    # Get parent directory
    script_dir = Path(__file__).parent
    parent_dir = script_dir.parent if script_dir.name == 'dylan_data' else script_dir

    # Load schedules
    print("\nLoading schedules...")
    schedules = load_schedules(parent_dir)

    if len(schedules) == 0:
        print("\n❌ ERROR: No schedules found!")
        print("\nPlease run:")
        print("  1. python dylan_data/convert_dylan_to_dp.py")
        print("  2. python tests/test_dylan_schedule.py")
        return

    print(f"\n✓ Loaded {len(schedules)} schedule(s)")

    # Create visualizations
    output_dir = parent_dir / "dylan_data"

    print("\nGenerating visualizations...")

    # Gantt chart
    plot_gantt_chart(schedules, output_dir / "comparison_gantt.png")

    # SOC trajectory
    plot_soc_trajectory(schedules, output_dir / "comparison_soc.png")

    # Charging comparison
    plot_charging_comparison(schedules, output_dir / "comparison_charging.png")

    # Metrics comparison
    plot_metrics_comparison(schedules, output_dir / "comparison_metrics.png")

    print("\n" + "="*70)
    print("VISUALIZATION COMPLETE!")
    print("="*70)
    print(f"\nGenerated files in {output_dir}:")
    print("  - comparison_gantt.png      (Activity timelines)")
    print("  - comparison_soc.png        (SOC trajectories)")
    print("  - comparison_charging.png   (Charging patterns)")
    print("  - comparison_metrics.png    (Key metrics)")

    if len(schedules) == 1:
        print("\nNote: Only one schedule available (DP).")
        print("To compare with CPLEX, run: jupyter notebook dylan_data/solution_analysis.ipynb")

    print("="*70)


if __name__ == "__main__":
    main()
