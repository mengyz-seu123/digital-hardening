from pathlib import Path
import csv
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D
import numpy as np
import paper_plot_style as style

HERE = Path(__file__).resolve().parent
DATA = HERE
BLUE, ORANGE, RED = style.BLUE, style.ORANGE, style.RED


def read_rows(name):
    with (DATA / name).open() as stream:
        return list(csv.DictReader(stream, delimiter=" "))


def read_contract():
    return {r["key"]: r["value"] for r in read_rows("contract.dat")}


def matrix_data(rows):
    states = list(dict.fromkeys(r["state_bits"] for r in rows))
    policies = list(dict.fromkeys(r["interface"] for r in rows))
    # A missing state/interface cell stays NaN and renders blank.
    area = np.full((len(states), len(policies)), np.nan)
    times = [float(next(r["latency_ns"] for r in rows if r["interface"] == policy))
             for policy in policies]
    for r in rows:
        i, j = states.index(r["state_bits"]), policies.index(r["interface"])
        area[i, j] = float(r["area_pct"])
    return states, policies, area, times


def cell_outline(ax, i, j, color, inset=.07, lw=1.3):
    ax.add_patch(Rectangle((j-.5+inset, i-.5+inset), 1-2*inset, 1-2*inset,
                           fill=False, edgecolor=color, linewidth=lw, zorder=5))


def heatmap(ax, rows, norm, cmap, seq_bits, proposed_bits):
    states, policies, values, times = matrix_data(rows)
    im = ax.pcolormesh(np.arange(len(policies)+1)-.5, np.arange(len(states)+1)-.5,
                       values, cmap=cmap, norm=norm, shading="flat", rasterized=False)
    ax.set(xlim=(-.5,len(policies)-.5), ylim=(len(states)-.5,-.5))
    ax.set_yticks(range(len(states)), ["[" + s.replace("-", ",") + "]" for s in states])
    ax.set_xticks(range(len(policies)),
                  [("F" if p.endswith("_fixed") else "B") + f"\n{t:.0f}"
                   for p, t in zip(policies, times)])
    ax.tick_params(axis="both", length=0, labelsize=6.5, pad=3)
    groups = {}
    for j, policy in enumerate(policies):
        r, c, _ = re.fullmatch(r"r(\d+)_f(\d+)_(.+)", policy).groups()
        groups.setdefault((int(r), int(c)), []).append(j)
    for (r, c), indices in groups.items():
        rlabel = f"{r//1000}k" if r >= 1000 else str(r)
        ax.text(np.mean(indices), -.30, f"{rlabel}/{c}",
                transform=ax.get_xaxis_transform(), ha="center", va="top", fontsize=6.5)
        ax.plot([min(indices)-.4, max(indices)+.4], [-.265, -.265],
                transform=ax.get_xaxis_transform(), color="#8E979E", lw=.5, clip_on=False)
    for i in range(len(states)):
        minima = np.flatnonzero(np.isclose(values[i], values[i].min(), atol=1e-5, rtol=0))
        for j in range(len(policies)):
            ax.add_patch(Rectangle((j-.5, i-.5), 1, 1, fill=False,
                                   edgecolor="white", linewidth=.65))
        for j in minima:
            cell_outline(ax, i, j, "#717980", .14, .55)
        # Tied minima are outlined; write the value once per row to avoid duplicate text.
        j = minima[0]
        ax.text(j, i, f"{values[i,j]:.1f}", ha="center", va="center", fontsize=6,
                color="white" if norm(values[i,j]) > .65 else "#20262B", zorder=6)
    for bits, color, inset in [(seq_bits, ORANGE, .04), (proposed_bits, BLUE, .12)]:
        i = states.index(bits)
        j = int(np.argmin(values[i]))
        cell_outline(ax, i, j, color, inset, 1.65)
    for spine in ax.spines.values():
        spine.set_color("#9BA4AB"); spine.set_linewidth(.55)
    return im, values


def main():
    pri = read_rows("primary_complete.dat")
    tra = read_rows("transfer_complete.dat")
    rank = read_rows("transfer_rank.dat")
    public = read_rows("public_posthoc.dat")
    contract = read_contract()
    cap = float(contract["area_cap_pct"])
    pbits = contract["primary_selected_bits"]
    sbits = contract["transfer_sequential_bits"]
    cbits = contract["transfer_proposed_bits"]
    plt.rcParams.update({"xtick.top": False, "ytick.right": False, "pdf.fonttype": 42})
    fig = plt.figure(figsize=(style.W_FULL, 3.90))
    cmap = LinearSegmentedColormap.from_list("area", ["#F4F7FA", "#CADBE7", "#729BBB", "#2B5373"])
    norm = Normalize(16, 50)
    ap = fig.add_axes([.145, .665, .335, .265])
    at = fig.add_axes([.655, .665, .325, .265])
    im, pv = heatmap(ap, pri, norm, cmap, pbits, pbits)
    _, tv = heatmap(at, tra, norm, cmap, sbits, cbits)
    style.panel_title(ap, f"(a) Primary: {pv.min():.2f}% optimum", pad=16)
    fig.text(.145, .945, f"{(pv <= cap).sum()}/{pv.size} pairs below {cap:g}%", fontsize=6.5)
    style.panel_title(at, f"(b) Transfer: {tv.min():.2f}% optimum", pad=16)
    fig.text(.655, .945, f"{(tv <= cap).sum()}/{tv.size} pairs below {cap:g}%", fontsize=6.5)
    cb = fig.colorbar(im, cax=fig.add_axes([.52, .665, .013, .265]), ticks=[20, 30, 40, 50])
    cb.ax.tick_params(labelsize=6.5, length=2, pad=2)
    cb.outline.set_linewidth(.45)
    cb.solids.set_rasterized(False)
    cb.set_label("Area overhead (%)", fontsize=7, labelpad=3)
    fig.text(.145, .526, "Columns: F/B and shutdown (ns); grouped by R/C (Ω/pF)", fontsize=6.5)
    handles = [Line2D([], [], color=ORANGE, marker="s", lw=1.1, markersize=4, label="Sequential"),
               Line2D([], [], color=BLUE, marker="o", lw=1.1, markersize=4, label="Proposed"),
               Rectangle((0,0),1,1,facecolor="none",edgecolor="#717980",lw=.6,
                         label="Row minima (ties outlined)")]
    fig.legend(handles=handles, loc="center", bbox_to_anchor=(.57,.483), ncol=3,
               frameon=False, fontsize=6.5, handlelength=1.5, columnspacing=1.5)
    # Relative differences retain the small rank crossing without a misleading truncated area axis.
    ar = fig.add_axes([.145, .13, .335, .275])
    by_bits = {r["bits"]: r for r in rank}
    base = by_bits[sbits]
    ar.axhline(0, color=ORANGE, linestyle="--", linewidth=1.2)
    vectors = {}
    for bit, row in by_bits.items():
        vector = (float(row["nominal_pct"])-float(base["nominal_pct"]),
                  float(row["complete_best_pct"])-float(base["complete_best_pct"]))
        vectors[bit] = vector
    for bits, color, marker, ls in [("2", "#7B858D", "s", "--"), ("1", BLUE, "o", "-")]:
        ar.plot([0,1], vectors[bits], color=color, marker=marker, markersize=4,
                linestyle=ls, linewidth=1.4)
    ar.text(-.03, vectors["1"][0]+.13, f"+{vectors['1'][0]:.3f}", fontsize=6.5)
    # Offset in points keeps the baseline label clear at the final print size.
    ar.annotate(f"Sequential [{sbits}]", xy=(1.76, 0), xytext=(0, 6),
                textcoords="offset points", ha="right", va="bottom",
                color=ORANGE, fontsize=6.5)
    ar.text(1.07, vectors["2"][1], f"[2]  {vectors['2'][1]:+.3f}", color="#59636C",
            fontsize=6.5, va="center")
    ar.text(1.07, vectors["1"][1], f"[0], [1]*\n{vectors['1'][1]:+.3f}", color=BLUE,
            fontsize=6.5, va="center")
    ar.set(xlim=(-.2,1.85), ylim=(-1.2,1.15), xticks=[0,1], xticklabels=["Nominal","Completed"],
           yticks=[-1,0,1])
    ar.set_ylabel("Δ area vs sequential (pp)", fontsize=7.5)
    ar.tick_params(labelsize=7, length=2.5)
    ar.grid(axis="y", color="#E5E9EC", linewidth=.5)
    ar.set_axisbelow(True)
    style.panel_title(ar, "(c) Transfer: nominal ranking reverses")
    fig.text(.145, .035, "* Equal area; tie-break selects [1].", fontsize=6.5)
    # Independent rows prevent fixed-11/fixed-26 from looking like a sequence of stages.
    ad = fig.add_axes([.655, .13, .325, .275])
    seq = [float(public[0]["sequential_nominal_pct"])] + [
        float(p["sequential_complete_pct"]) for p in public]
    proposed = [float(public[0]["proposed_nominal_pct"])] + [
        float(p["proposed_complete_pct"]) for p in public]
    ad.axvline(cap,color=RED,ls="--",lw=1, zorder=1)
    for y, (a,b) in enumerate(zip(seq,proposed)):
        ad.plot([a,b],[y,y],color="#BAC1C7",lw=1.5)
        ad.scatter(a,y,color=ORANGE,marker="s",s=20,zorder=4)
        ad.scatter(b,y,color=BLUE,marker="o",s=20,zorder=4)
        for x,color in [(a,ORANGE),(b,BLUE)]:
            ad.text(x,y-.17,f"{x:.3f}",color=color,ha="center",va="bottom",fontsize=6.5)
        if y:
            # Put gap labels on the blue side, away from the vertical area cap.
            ad.text(b+.08,y+.20,f"{a-b:.3f} pp",ha="left",va="top",fontsize=6.5)
    ad.set(xlim=(15.2,22.4),ylim=(2.7,-.85),yticks=[0,1,2],
           yticklabels=["Nominal","fixed-11","fixed-26"],xticks=[16,18,20,22])
    ad.set_xlabel("Mapped digital overhead (%)",fontsize=7.5)
    ad.tick_params(labelsize=7,length=2.5)
    ad.annotate(f"{cap:g}% cap", xy=(cap, -.75), xytext=(5, -2),
                textcoords="offset points", ha="left", va="top",
                color=RED, fontsize=6.5)
    style.panel_title(ad, f'(d) Public controller: post-hoc ε = {float(public[0]["epsilon"]):g}')
    for ax in (ar,ad):
        for spine in ax.spines.values():
            spine.set_color("#77828A"); spine.set_linewidth(.6)
    fig.savefig(HERE / "fig_complete_design.pdf", bbox_inches=None)
    fig.savefig(HERE / "fig_complete_design_preview.png", bbox_inches=None)
    plt.close(fig)
    print("Figure 4: wrote fig_complete_design.pdf "
          f"({len(pri)} Primary, {len(tra)} Transfer, {len(rank)} rank plans).")


if __name__ == "__main__":
    main()
