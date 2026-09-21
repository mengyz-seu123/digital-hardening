# Joint Controller Hardening

Code for joint state and interface hardening of gate-driver controllers under digital faults and high-dV/dt disturbances. The implementation constructs selective state-TMR candidates, checks digital feasibility, evaluates feedback protection, and ranks complete state/interface designs by mapped digital area.

## Contents

| Path | Contents |
| --- | --- |
| `code/src/`, `code/snapshots/` | Shared synthesis, state-TMR, fault-injection, and verification helpers |
| `code/v030/`, `code/v040/` | Finite candidate construction and search dependencies |
| `code/v091/`, `code/v095/` | Primary and transfer controller RTL, testbenches, catalogue and fault-pool builders |
| `code/v096/` | Finite-pool headroom certificate |
| `code/v110/`, `code/v120/`, `code/v130/`, `code/v132/` | Feedback stress models, interface guards and integration helpers |
| `code/v140/`, `code/v144/` | Completion-aware selection and interface separability audit |
| `figures/` | Two numerical figure generators, shared style, and six small summary tables |
| `lib/`, `licenses/`, `sources.lock` | Required Nangate45 Liberty library, upstream license and source hashes |
| `scripts/`, `tools.lock` | Dependency checks, reproduction driver and pinned digital EDA downloads |

Version-like directory names are implementation identifiers used by relative imports. Use the entry points below; shared modules also contain helpers for earlier benchmark interfaces. Raw simulation data, run logs, generated binaries, manuscript files and repository history are not distributed.

## Python setup and quick check

Use Python 3.11. Run commands from this directory. A Linux x86-64 checkout is required for the full EDA pipeline; the Python smoke test and figure generation also work on Windows.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/smoke_test.py
```

On Windows, replace the activation command with `.venv\Scripts\Activate.ps1` in PowerShell. The smoke test imports the main modules, verifies the shipped dependency hashes, and runs four receiver/metric boundary tests. It does not invoke EDA tools or establish experimental results.

## Regenerate numerical figures

```bash
python figures/fig_interface_metrics.py
python figures/fig_complete_design.py
```

PDFs and PNG previews are written beside the scripts. The text tables are plotting summaries supplied with the manuscript, not raw measurements or newly recomputed experimental evidence. Figure regeneration uses these tables directly; it does not run the simulation pipeline. The `public_posthoc.dat` panel is supplied as summary data only; its separate external-controller campaign is outside this minimal package.

| Table | Meaning |
| --- | --- |
| `contract.dat` | Area cap, shutdown deadline, event count and selected state bits |
| `primary_interface.dat` | Interface configurations, false trips, late shutdowns and worst delay |
| `primary_complete.dat` | Primary complete-design area and shutdown delay |
| `transfer_complete.dat` | Transfer complete-design area and shutdown delay |
| `transfer_rank.dat` | Nominal and integrated area ranking |
| `public_posthoc.dat` | Public-controller paired-design plotting summary |

Tables are space-delimited with a header. Area overhead is in percent, time in ns, resistance in ohms, and capacitance in pF. Plot axis ranges and some annotations are fixed to these tables.

## Full primary/transfer reproduction

Use Linux x86-64, including WSL2, and a checkout path without spaces. Install ngspice, a C compiler, binutils and the pinned digital tools:

```bash
sudo apt-get update
sudo apt-get install -y ngspice build-essential binutils
python scripts/fetch_tools.py --directory downloads
mkdir -p tools
tar -xzf downloads/oss-cad-suite-linux-x64-20260908.tgz -C tools
bash scripts/install_opensta.sh
source tools/oss-cad-suite/environment
export PATH="$PWD/tools/bin:$PATH"
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
python scripts/reproduce.py --check
python scripts/reproduce.py
```

The downloader verifies the public tool archives against `tools.lock`. The OSS CAD Suite supplies Yosys and Icarus Verilog; the OpenSTA wrapper uses the pinned Ubuntu package and the suite runtime. ngspice comes from the host distribution and is not pinned by this package. Record its version when comparing numerical outcomes.

The driver rebuilds both controller assets, mapped candidate catalogues and per-bit fault pools from source; evaluates primary interface protection; maps the surviving complete designs; then replays the interface contracts for the state survivors. It checks completion-aware acceptance flags and the expected 162 separability comparisons. No original raw-data archive or private CI artifact is required. Run time depends on the EDA host; this is a full experiment, not a quick demo.

Generated outputs remain under `code/*/results/`. The main summaries are:

- `code/v130/results/minloop-01/summary.json`
- `code/v140/results/completion-aware-01/summary.json`
- `code/v144/results/separability-audit-01/summary.json`

Runs refuse existing output directories. Use a fresh extraction to repeat the full experiment. Generated results are ignored by Git and may include local execution paths; only the source package is prepared for anonymous distribution.

## Experimental scope

The selection uses epsilon 0.02, a 20% digital-area overhead cap, and a 1000 ns shutdown deadline. The implementation retains the controller RTL, candidate construction, stress grids, harness reset/packing corrections and numerical thresholds. Packaging changes concern relative paths and removal of historical run identifiers and module narratives.

Area/timing evaluations use Nangate45 typical with pre-layout ideal wires. Certificates apply to the finite candidate catalogue and declared fault/stress population. The feedback models use imposed common-mode waveforms and ideal receiver sampling; they do not constitute measured device qualification. Figure regeneration and full simulation reproduction are separate workflows; newly generated results are not automatically substituted into the supplied plot tables.

## Third-party material

The Nangate45 library is included unchanged with its upstream copyright notice and `licenses/nangate45.txt`. Public source URLs, revisions and SHA-256 hashes are in `sources.lock`; tool downloads are in `tools.lock`. Third-party attribution is retained. The transfer controller is a clean-room model of public motor-control architecture requirements, not vendor RTL. No new license grant for the original research code is implied by this package.
