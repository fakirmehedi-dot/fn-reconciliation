"""
engine/report_order_wise.py
Generates the Order Wise reconciliation report matching the format:
  API Orders → Orchestrator → PSP Reconciled
  Package / Plan breakdown for Mar 01-21 vs Apr 01-21 (or custom periods)
"""
import pandas as pd
import numpy as np
import io

# ── Package / Plan extraction from "Plan Type" column ─────────────────────

PACKAGE_MAP = {
    # Futures
    "Futures Bolt":    ["futures bolt"],
    "Futures Legacy":  ["futures legacy"],
    "Futures Rapid":   ["futures rapid"],
    # CFD
    "Stellar Instant":        ["stellar instant"],
    "Stellar 1-Step":         ["stellar 1-step", "stellar-1-step"],
    "Stellar Lite 2-Step P1": ["stellar lite 2-step challenge p1", "stellar lite 2-step challenge p 1"],
    "Stellar 2-Step P1":      ["stellar 2-step challenge p1",      "stellar 2-step challenge p 1",
                               "stellar-2-step challenge p1"],
    "Stellar Lite 2-Step P2": ["stellar lite 2-step challenge p2", "stellar lite 2-step challenge p 2"],
    "Stellar 2-Step P2":      ["stellar 2-step challenge p2",      "stellar 2-step challenge p 2",
                               "stellar-2-step challenge p2"],
}

# Futures first, then CFD
PACKAGE_ORDER = list(PACKAGE_MAP.keys())
FUTURES_PACKAGES = ["Futures Bolt", "Futures Legacy", "Futures Rapid"]
CFD_PACKAGES     = [p for p in PACKAGE_ORDER if p not in FUTURES_PACKAGES]


def _parse_plan_type(pt: str):
    """Extract (package, plan_size) from Plan Type string like 'Futures Bolt Challenge | 50K'."""
    if not pt or pd.isna(pt):
        return ("Other", "Unknown")
    pt_lower = pt.lower().strip()
    # Determine size: part after '|'
    if '|' in pt:
        size = pt.split('|')[-1].strip().upper()
    else:
        size = "N/A"

    for pkg, patterns in PACKAGE_MAP.items():
        for pat in patterns:
            if pat in pt_lower:
                return (pkg, size)
    return ("Other", size)


def _enrich_api(api_df):
    """Add _package and _plan_size columns to API dataframe."""
    api = api_df.copy()
    pt_col = None
    for c in api.columns:
        if "plan type" in c.lower():
            pt_col = c
            break
    if pt_col:
        parsed = api[pt_col].apply(_parse_plan_type)
        api["_package"] = parsed.apply(lambda x: x[0])
        api["_plan"]    = parsed.apply(lambda x: x[1])
    else:
        api["_package"] = "Unknown"
        api["_plan"]    = "Unknown"
    return api


def _split_periods(api_df, date_col):
    """Split API rows into two periods for comparison."""
    api = api_df.copy()
    api[date_col] = pd.to_datetime(api[date_col], errors='coerce')

    # Detect periods automatically from data
    # Default: earliest 21-day period = Period 1, second 21-day = Period 2
    min_d = api[date_col].min()
    max_d = api[date_col].max()

    # Use Month 1 and Month 2 based on actual data
    months = sorted(api[date_col].dt.to_period("M").unique())
    if len(months) >= 2:
        m1, m2 = months[0], months[1]
        p1 = api[api[date_col].dt.to_period("M") == m1].copy()
        p2 = api[api[date_col].dt.to_period("M") == m2].copy()
        # Cap to day 21 of each month
        p1_start = p1[date_col].min().replace(day=1)
        p2_start = p2[date_col].min().replace(day=1)
        p1 = p1[p1[date_col] < (p1_start + pd.DateOffset(days=21))]
        p2 = p2[p2[date_col] < (p2_start + pd.DateOffset(days=21))]
        p1_label = f"{p1_start.strftime('%b')} 01–21"
        p2_label = f"{p2_start.strftime('%b')} 01–21"
    else:
        mid = min_d + (max_d - min_d) / 2
        p1 = api[api[date_col] <= mid].copy()
        p2 = api[api[date_col]  > mid].copy()
        p1_label = "Period 1"
        p2_label = "Period 2"

    return p1, p2, p1_label, p2_label


def _build_orch_set(phase1_results):
    """
    Build a set of API Transaction IDs matched at orchestrator level.
    Covers all Phase 1 banks (BP, PP, ZEN, Coinsbuy, Confirmo, TC Pay).
    """
    orch_ids = set()
    for key in ["bridgerpay", "payprocc", "coinsbuy", "zen", "confirmo", "tcpay"]:
        df = phase1_results.get(key, pd.DataFrame())
        if df.empty: continue
        # All reconciled + mismatch rows = found in orchestrator
        matched = df[df.get("Verdict", df.get("verdict", pd.Series())).isin(
            ["RECONCILED", "AMOUNT MISMATCH", "MATCHED (FEE DEDUCTED)"])]
        # Transaction ID is the API key column
        tid_col = None
        for c in matched.columns:
            if "transaction id" in c.lower() or c == "Transaction ID":
                tid_col = c
                break
        if not tid_col:
            # Try to get from index or first column that looks like a TID
            for c in matched.columns:
                if matched[c].astype(str).str.startswith(("BP_","PP_","ZP_","B2B_","CFM_","OP-"), na=False).any():
                    tid_col = c
                    break
        if tid_col:
            orch_ids.update(matched[tid_col].dropna().tolist())
    return orch_ids


def _build_psp_set(phase1_results, phase2_results):
    """
    Build a set of API Transaction IDs confirmed at PSP level.
    - Independent PSPs (ZEN, Coinsbuy, Confirmo, TC Pay): PSP = Phase 1 match
    - BP PSPs: use _api_tid column if present (= BP merchantOrderId = API TID)
               fallback: scan for BP_/PP_ prefixed values or map via bp_tid_to_moi
    - PP PSPs: map PP Payment Public ID → PP Merchant Order ID = API TID
    """
    psp_ids = set()

    # ── Independent PSPs — Phase 1 IS the PSP confirmation ───────────────────
    for key in ["coinsbuy", "zen", "confirmo", "tcpay"]:
        df = phase1_results.get(key, pd.DataFrame())
        if df.empty: continue
        matched = df[df.get("Verdict", pd.Series()).isin(
            ["RECONCILED", "AMOUNT MISMATCH", "MATCHED (FEE DEDUCTED)"])]
        for c in matched.columns:
            if matched[c].astype(str).str.startswith(
                    ("ZP_","B2B_","CFM_","OP-"), na=False).any():
                psp_ids.update(matched[c].dropna().tolist())
                break

    # ── Build lookup maps from raw orchestrator frames ────────────────────────
    bp_raw = phase1_results.get("bp_raw", pd.DataFrame())
    pp_raw = phase1_results.get("pp_raw", pd.DataFrame())

    bp_tid_to_moi = {}   # BP transactionId  → BP merchantOrderId (= API TID)
    bp_poi_to_moi = {}   # BP pspOrderId      → BP merchantOrderId
    if not bp_raw.empty:
        if "transactionId" in bp_raw.columns and "merchantOrderId" in bp_raw.columns:
            bp_tid_to_moi = dict(zip(bp_raw["transactionId"].dropna(),
                                      bp_raw["merchantOrderId"].dropna()))
        if "pspOrderId" in bp_raw.columns and "merchantOrderId" in bp_raw.columns:
            bp_poi_to_moi = dict(zip(bp_raw["pspOrderId"].dropna(),
                                      bp_raw["merchantOrderId"].dropna()))

    pp_pub_to_moi = {}   # PP Payment Public ID → PP Merchant Order ID (= API TID)
    if not pp_raw.empty:
        pub_col = None
        moi_col = None
        for c in pp_raw.columns:
            if "payment public" in c.lower(): pub_col = c
            if "merchant order" in c.lower(): moi_col = c
        if pub_col and moi_col:
            pp_pub_to_moi = dict(zip(pp_raw[pub_col].dropna(),
                                      pp_raw[moi_col].dropna()))

    # ── Phase 2 PSP results ───────────────────────────────────────────────────
    for psp_key, df2 in phase2_results.items():
        if df2 is None or df2.empty: continue
        rec = df2[df2.get("Verdict", pd.Series()) == "RECONCILED"]
        if rec.empty: continue

        # Priority 1: _api_tid column (added by recon_trustpayment, recon_paysafe_bp)
        if "_api_tid" in rec.columns:
            psp_ids.update(rec["_api_tid"].dropna().astype(str).tolist())
            continue

        # Priority 2: scan all columns for BP_ / PP_ prefixed values
        found = False
        for c in rec.columns:
            if c in ("Verdict","PSP","Diff (USD)","PSP_Amount","Orch_Amount"): continue
            sample = rec[c].dropna().astype(str)
            if sample.str.startswith("BP_", na=False).any():
                psp_ids.update(sample[sample.str.startswith("BP_")].tolist())
                found = True; break
            if sample.str.startswith("PP_", na=False).any():
                psp_ids.update(sample[sample.str.startswith("PP_")].tolist())
                found = True; break

        if found: continue

        # Priority 3: map via lookup tables (UUID keys → BP merchantOrderId)
        for c in rec.columns:
            if c in ("Verdict","PSP","Diff (USD)","PSP_Amount","Orch_Amount"): continue
            for val in rec[c].dropna().astype(str):
                if val in bp_tid_to_moi:
                    psp_ids.add(bp_tid_to_moi[val])
                elif val in bp_poi_to_moi:
                    psp_ids.add(bp_poi_to_moi[val])
                elif val in pp_pub_to_moi:
                    psp_ids.add(pp_pub_to_moi[val])

    return psp_ids


def build_order_wise(api_df, phase1_results, phase2_results):
    """
    Main entry point. Returns a dict of DataFrames ready to write to Excel.
    """
    # Find date and amount columns
    date_col = None
    for c in api_df.columns:
        if "created" in c.lower():
            date_col = c
            break
    amt_col = None
    for c in api_df.columns:
        if "grand total" in c.lower():
            amt_col = c
            break
    tid_col = None
    for c in api_df.columns:
        if "transaction id" in c.lower():
            tid_col = c
            break

    if not date_col or not tid_col:
        return {}

    # Enrich API with package/plan
    api = _enrich_api(api_df)
    api["_gt"] = pd.to_numeric(api.get(amt_col, pd.Series(0)), errors='coerce').fillna(0)

    # Split into two periods
    p1, p2, p1_label, p2_label = _split_periods(api, date_col)

    # Build orchestrator and PSP sets
    orch_ids = _build_orch_set(phase1_results)
    has_phase2 = bool(phase2_results)
    psp_ids = _build_psp_set(phase1_results, phase2_results) if has_phase2 else orch_ids.copy()

    def count_for(period_df):
        tids = set(period_df[tid_col].dropna())
        api_n   = len(period_df)
        api_rev = period_df["_gt"].sum()
        orch_n  = len([t for t in tids if t in orch_ids])
        orch_rev = period_df[period_df[tid_col].isin(orch_ids)]["_gt"].sum()
        psp_n   = len([t for t in tids if t in psp_ids])
        psp_rev = period_df[period_df[tid_col].isin(psp_ids)]["_gt"].sum()
        return dict(api_n=api_n, api_rev=api_rev,
                    orch_n=orch_n, orch_rev=orch_rev,
                    diff_orch=api_n - orch_n, diff_orch_rev=api_rev - orch_rev,
                    psp_n=psp_n, psp_rev=psp_rev,
                    diff_psp=api_n - psp_n, diff_psp_rev=api_rev - psp_rev)

    # Build rows for Order Wise table
    rows = []
    for pkg in PACKAGE_ORDER:
        p1_pkg = p1[p1["_package"] == pkg]
        p2_pkg = p2[p2["_package"] == pkg]
        sizes_p1 = sorted(p1_pkg["_plan"].unique(), key=lambda x: int(''.join(filter(str.isdigit, x)) or 0))
        sizes_p2 = sorted(p2_pkg["_plan"].unique(), key=lambda x: int(''.join(filter(str.isdigit, x)) or 0))
        all_sizes = sorted(set(sizes_p1) | set(sizes_p2),
                           key=lambda x: int(''.join(filter(str.isdigit, x)) or 0))

        pkg_totals_p1 = count_for(p1_pkg)
        pkg_totals_p2 = count_for(p2_pkg)

        for size in all_sizes:
            c1 = count_for(p1_pkg[p1_pkg["_plan"] == size])
            c2 = count_for(p2_pkg[p2_pkg["_plan"] == size])
            rows.append({
                "Package": pkg, "Plan": size,
                **{f"P1_{k}": v for k, v in c1.items()},
                **{f"P2_{k}": v for k, v in c2.items()},
                "_is_total": False, "_pkg_sort": PACKAGE_ORDER.index(pkg)
            })

        # Package subtotal
        rows.append({
            "Package": f"Total {pkg}", "Plan": "",
            **{f"P1_{k}": v for k, v in pkg_totals_p1.items()},
            **{f"P2_{k}": v for k, v in pkg_totals_p2.items()},
            "_is_total": True, "_pkg_sort": PACKAGE_ORDER.index(pkg)
        })

    # Grand total
    c1g = count_for(p1)
    c2g = count_for(p2)
    rows.append({
        "Package": "GRAND TOTAL", "Plan": "",
        **{f"P1_{k}": v for k, v in c1g.items()},
        **{f"P2_{k}": v for k, v in c2g.items()},
        "_is_total": True, "_pkg_sort": 999
    })

    return {
        "rows": rows,
        "p1_label": p1_label,
        "p2_label": p2_label,
        "grand_p1": c1g,
        "grand_p2": c2g,
    }


def write_order_wise_excel(api_df, phase1_results, phase2_results, out_path=None):
    """Write the Order Wise report to an Excel buffer."""
    data = build_order_wise(api_df, phase1_results, phase2_results)
    if not data:
        return None

    rows     = data["rows"]
    p1_label = data["p1_label"]
    p2_label = data["p2_label"]

    buf = io.BytesIO()
    import xlsxwriter
    wb = xlsxwriter.Workbook(buf, {'nan_inf_to_errors': True})

    # ── Formats ────────────────────────────────────────────────────────────
    hdr = wb.add_format({'bold': True, 'bg_color': '#1e3a5f', 'font_color': '#FFFFFF',
                          'align': 'center', 'valign': 'vcenter', 'border': 1})
    subhdr = wb.add_format({'bold': True, 'bg_color': '#2e6da4', 'font_color': '#FFFFFF',
                             'align': 'center', 'valign': 'vcenter', 'border': 1})
    pkg_fmt  = wb.add_format({'bold': True, 'bg_color': '#e8f0fb', 'border': 1})
    tot_fmt  = wb.add_format({'bold': True, 'bg_color': '#c6d9f1', 'border': 1})
    grand_fmt= wb.add_format({'bold': True, 'bg_color': '#1e3a5f', 'font_color': '#FFFFFF', 'border': 1})
    cell_fmt = wb.add_format({'border': 1, 'align': 'right'})
    name_fmt = wb.add_format({'border': 1})
    diff_fmt = wb.add_format({'border': 1, 'align': 'right', 'font_color': '#c00000'})
    num2_fmt = wb.add_format({'border': 1, 'align': 'right', 'num_format': '#,##0.00'})
    num2d_fmt= wb.add_format({'border': 1, 'align': 'right', 'num_format': '#,##0.00', 'font_color': '#c00000'})

    # ── Order Wise sheet ───────────────────────────────────────────────────
    for sheet_suffix, field_prefix, amt_label in [
        ("Order Wise", "n", "Orders"),
        ("Amount Wise", "rev", "Amount (USD)")
    ]:
        ws = wb.add_worksheet(f"Coverage Detail ({sheet_suffix})")
        ws.freeze_panes(4, 2)
        ws.set_column("A:A", 24)
        ws.set_column("B:B", 8)
        ws.set_column("C:L", 14)

        title = f"Revenue Reconciliation {sheet_suffix}— API → Orchestrator → PSP  |  {p1_label} vs {p2_label}"
        ws.merge_range("A1:K1", title, hdr)

        # Header row 2
        ws.merge_range("A2:B2", "Package / Plan", subhdr)
        ws.merge_range("C2:G2", p1_label, subhdr)
        ws.merge_range("H2:L2", p2_label, subhdr)

        # Header row 3
        for c, t in enumerate(["", "", "API", "Orchestrator", "Diff (API-Orch)",
                                "PSP Reconciled", "Diff (API-PSP)",
                                "API", "Orchestrator", "Diff (API-Orch)",
                                "PSP Reconciled", "Diff (API-PSP)"]):
            ws.write(2, c, t if c > 1 else ("Package" if c == 0 else "Plan"), subhdr)

        # Header row 4 (sub-label)
        for c, t in enumerate(["", ""] + [amt_label]*10):
            ws.write(3, c, t, subhdr)

        row_i = 4
        prev_pkg = None
        for r in rows:
            pkg = r["Package"]
            plan = r["Plan"]
            is_total = r["_is_total"]
            is_grand = (pkg == "GRAND TOTAL")

            # Section header for package group
            if not is_total and not is_grand and pkg != prev_pkg:
                section = "── FUTURES ──" if pkg == FUTURES_PACKAGES[0] and prev_pkg is None else \
                          ("── CFD ──" if pkg == CFD_PACKAGES[0] else "")
                if section:
                    ws.merge_range(row_i, 0, row_i, 10, section, pkg_fmt)
                    row_i += 1
                prev_pkg = pkg

            f = grand_fmt if is_grand else (tot_fmt if is_total else name_fmt)
            fn = grand_fmt if is_grand else (tot_fmt if is_total else cell_fmt)
            fd = grand_fmt if is_grand else (tot_fmt if is_total else diff_fmt)

            if field_prefix == "rev":
                fn2 = grand_fmt if is_grand else (tot_fmt if is_total else num2_fmt)
                fd2 = grand_fmt if is_grand else (tot_fmt if is_total else num2d_fmt)
            else:
                fn2 = fn
                fd2 = fd

            vals = [
                pkg if is_grand or is_total else pkg,
                plan,
                r[f"P1_api_{field_prefix}"],
                r[f"P1_orch_{field_prefix}"],
                r[f"P1_diff_orch" + ("_rev" if field_prefix=="rev" else "")],
                r[f"P1_psp_{field_prefix}"],
                r[f"P1_diff_psp" + ("_rev" if field_prefix=="rev" else "")],
                r[f"P2_api_{field_prefix}"],
                r[f"P2_orch_{field_prefix}"],
                r[f"P2_diff_orch" + ("_rev" if field_prefix=="rev" else "")],
                r[f"P2_psp_{field_prefix}"],
                r[f"P2_diff_psp" + ("_rev" if field_prefix=="rev" else "")],
            ]
            ws.write(row_i, 0, vals[0], f)
            ws.write(row_i, 1, vals[1], f)
            for ci, (v, fmt) in enumerate(zip(vals[2:], [fn2, fn2, fd2, fn2, fd2,
                                                           fn2, fn2, fd2, fn2, fd2])):
                ws.write(row_i, ci + 2, v, fmt)
            row_i += 1

    wb.close()
    buf.seek(0)
    return buf
