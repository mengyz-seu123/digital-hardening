import matplotlib as mpl

# Shared semantic palette for plots and editable schematics.
BLUE, ORANGE, RED = "#2369A0", "#C87932", "#B02020"
INK, GREY, EDGE = "#20262B", "#7B858D", "#BAC1C7"
PALE = "#F4F7FA"
SEQ, CA, CAP, PASS = ORANGE, BLUE, RED, "#2E8C5A"
PANEL_SIZE = 8

W_HALF = 3.15
W_FULL = 6.50

mpl.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
        "font.weight": "normal",
        "font.size": 8,
        "axes.labelsize": 8,
        "axes.titlesize": 9,
        "axes.labelweight": "normal",
        "axes.titleweight": "bold",
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 7,
        "axes.spines.top": True,
        "axes.spines.right": True,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "xtick.top": False,
        "ytick.right": False,
        "xtick.major.size": 3.5,
        "ytick.major.size": 3.5,
        "mathtext.default": "regular",
        "figure.dpi": 300,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.05,
        "axes.grid": False,
        "text.color": INK,
        "axes.labelcolor": INK,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    }
)


def panel_title(ax, title, pad=8):
    """Align panel headings to the plotting area's left edge."""
    return ax.set_title(title, loc="left", pad=pad,
                        fontsize=PANEL_SIZE, fontweight="bold", color=INK)
