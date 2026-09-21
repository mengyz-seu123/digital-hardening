from pathlib import Path
import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import Rectangle
import numpy as np
import paper_plot_style as style

HERE = Path(__file__).resolve().parent
DATA = HERE
BLUE, RED = style.BLUE, style.RED


def read_rows(name):
    with (DATA / name).open() as stream:
        return list(csv.DictReader(stream, delimiter=" "))


def main():
    rows = read_rows("primary_interface.dat")
    contract = {r["key"]: r["value"] for r in read_rows("contract.dat")}
    rc = list(dict.fromkeys((int(r["r_ohm"]), int(r["filter_pf"])) for r in rows))
    methods = ["raw", "fixed", "blanking"]
    trips = np.full((6, 3), np.nan)
    delay = np.full((6, 3), np.nan)
    for r in rows:
        i = rc.index((int(r["r_ohm"]), int(r["filter_pf"])))
        j = methods.index(r["method"])
        trips[i, j] = int(r["false_trips"])
        delay[i, j] = float(r["worst_ns"])
    # The selection is resolved through the complete-pair data, not the fastest interface.
    complete = read_rows("primary_complete.dat")
    best = min(complete, key=lambda r: float(r["area_pct"]))
    best_policy = next(r for r in rows if
        f"r{r['r_ohm']}_f{r['filter_pf']}_{r['method']}" == best["interface"])
    selected = (rc.index((int(best_policy["r_ohm"]), int(best_policy["filter_pf"]))),
                methods.index(best_policy["method"]))
    deadline = float(contract["sc_deadline_ns"])
    n_noise = int(contract["dvdt_cases"])
    plt.rcParams.update({"xtick.top": False, "ytick.right": False, "pdf.fonttype": 42})
    fig = plt.figure(figsize=(style.W_HALF, 1.95))
    axs = [fig.add_axes([.20, .30, .33, .53]),
           fig.add_axes([.65, .30, .33, .53])]
    cmap_trip = LinearSegmentedColormap.from_list("trip", ["#FFFFFF", "#F4D8AE", "#B36019"])
    cmap_time = LinearSegmentedColormap.from_list("time", ["#F4F7FA", "#AAC6DD", "#376987"])
    norms = [Normalize(0, n_noise), Normalize(700, 1800)]
    for k, (ax, values, cmap) in enumerate(zip(axs, [trips, delay], [cmap_trip, cmap_time])):
        im = ax.pcolormesh(np.arange(4)-.5, np.arange(7)-.5, values,
                           cmap=cmap, norm=norms[k], shading="flat", rasterized=False)
        ax.set(xlim=(-.5,2.5), ylim=(5.5,-.5))
        ax.set_xticks(range(3), ["Raw", "Sample", "Blank"])
        ax.xaxis.tick_top()
        ax.tick_params(axis="both", length=0, labelsize=6, pad=3)
        ax.set_yticks(range(6))
        if k == 0:
            ax.set_yticklabels([f"{r // 1000}k/{c}" if r >= 1000 else f"{r}/{c}"
                                for r, c in rc], fontsize=6.5)
        else:
            ax.set_yticklabels([])
        for i in range(6):
            for j in range(3):
                value = values[i, j]
                color = "#888888" if k == 0 and value == 0 else (
                    "white" if norms[k](value) > .64 else "#242424")
                ax.text(j, i, f"{value:.0f}", ha="center", va="center",
                        fontsize=6.5, color=color)
                ax.add_patch(Rectangle((j-.5, i-.5), 1, 1, fill=False,
                                       edgecolor="white", linewidth=.65))
                if k == 1 and value > deadline:
                    ax.add_patch(Rectangle((j-.46, i-.46), .92, .92,
                                           fill=False, edgecolor=RED, linewidth=1.25))
        si, sj = selected
        ax.add_patch(Rectangle((sj-.45, si-.45), .9, .9, fill=False,
                               edgecolor=BLUE, linewidth=1.5))
        for spine in ax.spines.values():
            spine.set_color("#B5BDC3")
            spine.set_linewidth(.5)
        cb_ax = fig.add_axes([ax.get_position().x0, .195, .33, .026])
        cb = fig.colorbar(im, cax=cb_ax, orientation="horizontal",
                         ticks=[0, n_noise] if k == 0 else [700, 1000, 1800])
        cb.ax.tick_params(labelsize=6, length=2, pad=1)
        cb.outline.set_linewidth(.4)
        cb.solids.set_rasterized(False)
    style.panel_title(axs[0], f"(a) Trips / {n_noise}", pad=16)
    style.panel_title(axs[1], "(b) Shutdown (ns)", pad=16)
    fig.text(.005, .857, "R/C\n(Ω/pF)", fontsize=6.5, va="center")
    fig.text(.20, .082, "Blue outline: selected by complete area", color=BLUE, fontsize=6.5)
    fig.text(.20, .023, f"Red outline: exceeds {deadline:g} ns", color=RED, fontsize=6.5)
    fig.savefig(HERE / "fig_interface_metrics.pdf", bbox_inches=None)
    fig.savefig(HERE / "fig_interface_metrics_preview.png", bbox_inches=None)
    plt.close(fig)
    print(f"Figure 3: wrote fig_interface_metrics.pdf ({len(rows)} policies).")


if __name__ == "__main__":
    main()
