from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import primary_interface as s
import transfer_interface as p0

PRIMARY_GUARD = ROOT / "rtl/primary_guard.v"
AUTHORITATIVE_RUN = "local-reproduction"
AUTHORITATIVE_ARTIFACT = "local-completion-results"

PRIMARY_FIELDS = (
    "normal_pass", "normal_traces",
    "noise_events", "noise_false_trips", "noise_unexpected", "noise_overlap",
    "short_events", "short_late", "short_missed", "short_false_trips",
    "short_unexpected", "short_overlap", "worst_shutdown_ns",
    "marker_absent_events", "marker_absent_false_trips", "marker_robust_pass",
    "stress_contract_pass",
)

TRANSFER_FIELDS = (
    "normal_pass", "normal_traces",
    "noise_events", "noise_false_trips",
    "short_events", "short_late", "short_missed", "worst_shutdown_ns",
    "marker_absent_false_trips", "stress_pass",
)


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def interface_key(row):
    return (
        int(row["r_ohm"]), int(row["filter_pf"]), str(row["method"]),
        int(row["samples"]), int(row["guard_cycles"]),
    )


def normalized_signature(row, fields):
    out = {}
    for field in fields:
        value = row.get(field)
        if isinstance(value, float):
            value = round(value, 6)
        out[field] = value
    return out


def plans_from_summary(rows):
    plans = []
    seen = set()
    for row in rows:
        key = (int(row["r_ohm"]), int(row["filter_pf"]),
               int(row["samples"]), int(row["guard_cycles"]))
        if key in seen:
            continue
        seen.add(key)
        plan = dict(
            r_ohm=key[0], filter_pf=key[1], samples=key[2], guard_cycles=key[3]
        )
        if "asserted_pullup_mA" in row:
            plan["asserted_pullup_mA"] = row["asserted_pullup_mA"]
        plans.append(plan)
    return plans


def candidate_states(folder: Path):
    states = []
    for d in sorted(folder.glob("s??_tmr_*")):
        rtl = d / "candidate_top.v"
        meta = d / "meta.json"
        if not rtl.exists() or not meta.exists():
            raise RuntimeError(("incomplete authoritative candidate", d))
        m = json.loads(meta.read_text())
        states.append(dict(
            label=d.name,
            plan_id=d.name.split("_", 1)[1],
            selected=m["selected"],
            rtl=rtl,
        ))
    return states


def audit_against_authoritative(design, states, rows_by_state, authoritative_rows, fields):
    reference = {
        interface_key(r): normalized_signature(r, fields)
        for r in authoritative_rows
    }
    if len(reference) != len(authoritative_rows):
        raise RuntimeError((design, "duplicate authoritative interface keys"))

    mismatches = []
    comparisons = 0
    for state in states:
        state_id = state["plan_id"]
        candidate_rows = rows_by_state[state_id]
        candidate = {
            interface_key(r): normalized_signature(r, fields)
            for r in candidate_rows
        }
        if len(candidate) != len(candidate_rows):
            raise RuntimeError((design, state_id, "duplicate replay interface keys"))
        if set(candidate) != set(reference):
            mismatches.append({
                "state_plan_id": state_id,
                "kind": "interface_key_set",
                "reference_keys": sorted(map(list, reference)),
                "candidate_keys": sorted(map(list, candidate)),
            })
            continue
        for key in sorted(reference):
            comparisons += 1
            if candidate[key] != reference[key]:
                mismatches.append({
                    "state_plan_id": state_id,
                    "interface_key": list(key),
                    "authoritative": reference[key],
                    "replayed": candidate[key],
                })

    return dict(
        design=design,
        state_plan_count=len(states),
        interface_count=len(reference),
        evaluated_state_interface_rows=sum(len(v) for v in rows_by_state.values()),
        authoritative_signature_comparisons=comparisons,
        mismatch_count=len(mismatches),
        separable=(len(mismatches) == 0),
        mismatches=mismatches,
        states=[{k: v for k, v in x.items() if k != "rtl"} for x in states],
    )








def primary_audit(artifact: Path, out: Path):
    s.GUARD_RTL = PRIMARY_GUARD
    gen = artifact / "results/completion/primary/generated"
    reference_path = artifact / "results/interface/interface_summary.json"
    states = candidate_states(gen)
    reference_rows = json.loads(reference_path.read_text())
    plans = plans_from_summary(reference_rows)
    rows_by_state = {}

    for si, state in enumerate(states):
        state_rows = []
        for pi, plan in enumerate(plans):
            rows, _ = s.evaluate_interface(
                out / "replay" / f"s{si:02d}" / f"p{pi:02d}", state["rtl"], plan
            )
            for row in rows:
                row = dict(row)
                row["state_plan_id"] = state["plan_id"]
                row["state_selected"] = state["selected"]
                state_rows.append(row)
        rows_by_state[state["plan_id"]] = state_rows
        print("SEPARABILITY_PRIMARY_STATE", si, state["plan_id"], len(state_rows), flush=True)

    result = audit_against_authoritative(
        "primary", states, rows_by_state, reference_rows, PRIMARY_FIELDS
    )
    result["count_contract_pass"] = (
        result["state_plan_count"] == 7 and result["interface_count"] == 18
    )
    result["frozen_plans"] = plans
    dump(out / "rows.json", rows_by_state)
    dump(out / "summary.json", result)
    return result


def transfer_audit(artifact: Path, out: Path):
    gen = artifact / "results/completion/transfer/generated"
    reference_path = artifact / "results/completion/transfer/interface_summary.json"
    states = candidate_states(gen)
    reference_rows = json.loads(reference_path.read_text())
    plans = plans_from_summary(reference_rows)
    rows_by_state = {}

    for si, state in enumerate(states):
        state_rows = []
        for pi, plan in enumerate(plans):
            rows, _ = p0.evaluate_point(
                out / "replay" / f"s{si:02d}" / f"p{pi:02d}", state["rtl"], plan
            )
            for row in rows:
                row = dict(row)
                row["state_plan_id"] = state["plan_id"]
                row["state_selected"] = state["selected"]
                state_rows.append(row)
        rows_by_state[state["plan_id"]] = state_rows
        print("SEPARABILITY_TRANSFER_STATE", si, state["plan_id"], len(state_rows), flush=True)

    result = audit_against_authoritative(
        "transfer", states, rows_by_state, reference_rows, TRANSFER_FIELDS
    )
    result["count_contract_pass"] = (
        result["state_plan_count"] == 4 and result["interface_count"] == 9
    )
    result["frozen_plans"] = plans
    dump(out / "rows.json", rows_by_state)
    dump(out / "summary.json", result)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    artifact = args.artifact_root.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)

    parent_summary = artifact / "results/completion/summary.json"
    if not parent_summary.exists():
        raise RuntimeError(("authoritative artifact layout missing", parent_summary))

    dump(out / "protocol.json", dict(
        status="predeclared_separability_audit",
        parent_science_run=AUTHORITATIVE_RUN,
        parent_artifact=AUTHORITATIVE_ARTIFACT,
        state_scope="all epsilon-optimal state RTLs emitted by the authoritative declared run",
        interface_scope="all frozen interface candidates, including passing and failing columns",
        comparison="exact declared outcome signatures against authoritative interface evidence",
        new_mapping=False,
        new_candidate=False,
        new_interface=False,
        new_stress_point=False,
        recalibration=False,
        new_threshold=False,
        retuning=False,
        failure_policy="any mismatch invalidates shared column-discharge evidence for the affected interface",
    ))

    primary = primary_audit(artifact, out / "primary")
    transfer = transfer_audit(artifact, out / "transfer")

    total_rows = primary["evaluated_state_interface_rows"] + transfer["evaluated_state_interface_rows"]
    total_cmp = primary["authoritative_signature_comparisons"] + transfer["authoritative_signature_comparisons"]
    total_bad = primary["mismatch_count"] + transfer["mismatch_count"]
    summary = dict(
        status="completed",
        primary=primary,
        transfer=transfer,
        all_separable=bool(primary["separable"] and transfer["separable"]),
        count_contract_pass=bool(primary["count_contract_pass"] and transfer["count_contract_pass"]),
        total_state_interface_rows=total_rows,
        total_signature_comparisons=total_cmp,
        total_mismatches=total_bad,
        claim_boundary=(
            "Exhaustive over the declared frozen declared epsilon-optimal state plans, "
            "interface catalogue, and stress/deadline/normal campaigns only; not a "
            "proof for arbitrary unseen waveforms, hardware, or other catalogues."
        ),
    )
    dump(out / "summary.json", summary)
    print("SEPARABILITY_SEPARABILITY_COMPLETE", json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
