#!/usr/bin/env python3
"""
Plot charging participation and distribution charts from CSV data.

Two types of charts:
1. Participation chart: Shows % of simulation runs with charging at each hour
   (bars don't sum to 100% - can exceed 100% if multiple activities charge)

2. Distribution chart: Shows distribution of all charging events
   (all bars sum to exactly 100% - shows where/when charging occurred)

Requires: matplotlib
Install with: pip3 install matplotlib
"""

import pandas as pd
import matplotlib.pyplot as plt
import sys
import os



def plot_charging_distribution(csv_file, output_file=None, title=None):
    """
    Create a stacked bar chart showing the DISTRIBUTION of charging events.

    Parameters:
    -----------
    csv_file : str
        Path to charging_participation_by_hour.csv
    output_file : str
        Path to save figure (optional)
    title : str
        Custom title for the plot
    """
    # Load data
    df = pd.read_csv(csv_file, index_col='hour')

    print(f"Loaded data from: {csv_file}")
    print(f"Activity types: {list(df.columns)}")

    # Calculate total charging across all hours and activities
    total_charging = df.sum().sum()

    if total_charging == 0:
        print("Warning: No charging events found in data!")
        return None, None

    # Normalize so all values sum to 100%
    df_normalized = (df / total_charging) * 100

    # Verify it sums to 100%
    total_check = df_normalized.sum().sum()
    print(f"Total normalized: {total_check:.2f}% (should be 100%)")

    # Ensure all hours 0-23 are present
    all_hours = pd.DataFrame({'hour': range(24)}).set_index('hour')
    df_normalized = all_hours.join(df_normalized).fillna(0)

    # Sort columns by total participation
    column_totals = df_normalized.sum(axis=0).sort_values(ascending=False)
    df_normalized = df_normalized[column_totals.index]

    # If matplotlib not available, return the data
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Matplotlib not available. Returning normalized data only.")
        return None, df_normalized

    # Create figure
    fig, ax = plt.subplots(figsize=(14, 6))

    # Create stacked bar chart
    df_normalized.plot(
        kind='bar',
        stacked=True,
        ax=ax,
        width=0.8,
        edgecolor='none'
    )

    # Customize plot
    ax.set_xlabel('Hour of the Day', fontsize=12, fontweight='bold')
    ax.set_ylabel('Percentage of total charging', fontsize=12, fontweight='bold')

    if title:
        ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
    else:
        ax.set_title('Distribution of Charging Events by Activity Type and Hour',
                     fontsize=14, fontweight='bold', pad=20)

    # Format x-axis labels
    hours_labels = [f"{h}:00" for h in range(24)]
    ax.set_xticklabels(hours_labels, rotation=45, ha='right')

    # Format y-axis as percentage
    ax.set_ylim(0, None)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f'{y:.1f}%'))

    # Grid
    ax.grid(axis='y', alpha=0.3, linestyle='--', linewidth=0.5)
    ax.set_axisbelow(True)

    # Legend with total percentages
    legend_labels = []
    for col in df_normalized.columns:
        # total_pct = df_normalized[col].sum()
        # legend_labels.append(f'{col} ({total_pct:.1f}%)')
        legend_labels.append(f'{col}')

    ax.legend(legend_labels, title='Activity',
             bbox_to_anchor=(1.05, 1), loc='upper left',
             frameon=True)

    # # Add text annotation showing total
    # fig.text(0.99, 0.01, f'Total: {total_check:.1f}%',
    #          ha='right', va='bottom', fontsize=9, style='italic', color='gray')

    # Tight layout
    plt.tight_layout()

    # Save if output file specified
    if output_file:
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        print(f"Saved distribution chart to: {output_file}")

    plt.show()

    return fig, ax


def main():
    """Main function."""
    # Default files
    csv_file = "testing_latest/charging_participation_results/charging_participation_by_hour.csv"
    output_file_distribution = "testing_latest/charging_participation_results/charging_distribution_chart.png"

    if not os.path.exists(csv_file):
        print(f"Error: Data file not found: {csv_file}")
        print("\nEither:")
        print("  - Pass an all_schedules.csv path to this script, OR")
        print("  - Generate charging_participation_by_hour.csv first")
        sys.exit(1)

    print("Creating charging distribution chart...")
    plot_charging_distribution(csv_file, output_file_distribution)

if __name__ == "__main__":
    main()
