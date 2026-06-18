"""
engine/report_summary.py
Generates the two summary Excel reports:
  1. Full Reconciliation Summary  — API | Orch | Diff | PSP | Diff (Orders + Revenue)
  2. Comparison Summary           — Package/Plan × Period (Order Wise + Amount Wise combined)
"""
import io
import pandas as pd
import numpy as np
import xlsxwriter
from engine.report_order_wise import build_order_wise, _build_orch_set, _build_psp_set, PACKAGE_ORDER, FUTURES_PACKAGES, CFD_PACKAGES


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def compute_summary_stats(api_df, results):
    """
    Return dict with:
      api_orders, orch_orders, diff_orch, psp_orders, diff_psp
      api_rev,    orch_rev,    diff_orch_rev, psp_rev, diff_psp_rev
    """
    orch_ids = _build_orch_set(results)
    psp_ids  = _build_psp_set(results, results.get("phase2", {}))

    tid_col = None
    for c in api_df.columns:
        if "transaction id" in c.lower():
            tid_col = c; break
    gt_col = None
    for c in api_df.columns:
        if "grand total" in c.lower():
            gt_col = c; break

    if not tid_col:
        return {}

    api_df = api_df.copy()
    api_df["_gt"] = pd.to_numeric(api_df.get(gt_col, 0), errors="coerce").fillna(0)
    tids = set(api_df[tid_col].dropna())

    # Also collect Tracking IDs for Coinsbuy/Confirmo matching
    trk_col = None
    for c in api_df.columns:
        if "tracking id" in c.lower():
            trk_col = c; break
    trk_ids = set(api_df[trk_col].dropna()) if trk_col else set()

    api_orders   = len(api_df)
    # An order is matched if EITHER its Transaction ID or Tracking ID is in the set
    orch_mask = api_df[tid_col].isin(orch_ids)
    if trk_col:
        orch_mask = orch_mask | api_df[trk_col].isin(orch_ids)
    psp_mask = api_df[tid_col].isin(psp_ids)
    if trk_col:
        psp_mask = psp_mask | api_df[trk_col].isin(psp_ids)

    orch_orders  = int(orch_mask.sum())
    psp_orders   = int(psp_mask.sum())

    api_rev  = api_df["_gt"].sum()
    orch_rev = api_df[orch_mask]["_gt"].sum()
    psp_rev  = api_df[psp_mask]["_gt"].sum()

    return dict(
        api_orders=api_orders, orch_orders=orch_orders,
        diff_orch=api_orders - orch_orders,
        psp_orders=psp_orders, diff_psp=api_orders - psp_orders,
        api_rev=api_rev, orch_rev=orch_rev,
        diff_orch_rev=api_rev - orch_rev,
        psp_rev=psp_rev, diff_psp_rev=api_rev - psp_rev,
    )


def _wb_formats(wb):
    """Return common formats dict for a workbook."""
    return {
        "title": wb.add_format({"bold": True, "bg_color": "#1e3a5f", "font_color": "#FFFFFF",
                                  "align": "center", "valign": "vcenter", "font_size": 12, "border": 1}),
        "hdr1":  wb.add_format({"bold": True, "bg_color": "#1e3a5f", "font_color": "#FFFFFF",
                                  "align": "center", "valign": "vcenter", "border": 1}),
        "hdr2":  wb.add_format({"bold": True, "bg_color": "#2e6da4", "font_color": "#FFFFFF",
                                  "align": "center", "valign": "vcenter", "border": 1}),
        "hdr3":  wb.add_format({"bold": True, "bg_color": "#4a90d9", "font_color": "#FFFFFF",
                                  "align": "center", "valign": "vcenter", "border": 1}),
        "val":   wb.add_format({"border": 1, "align": "right", "num_format": "#,##0"}),
        "val2":  wb.add_format({"border": 1, "align": "right", "num_format": "#,##0.00"}),
        "diff":  wb.add_format({"border": 1, "align": "right", "num_format": "#,##0",
                                  "font_color": "#C00000"}),
        "diff2": wb.add_format({"border": 1, "align": "right", "num_format": "#,##0.00",
                                  "font_color": "#C00000"}),
        "name":  wb.add_format({"border": 1}),
        "pkg":   wb.add_format({"bold": True, "bg_color": "#e8f0fb", "border": 1}),
        "tot":   wb.add_format({"bold": True, "bg_color": "#c6d9f1", "border": 1}),
        "totv":  wb.add_format({"bold": True, "bg_color": "#c6d9f1", "border": 1,
                                  "align": "right", "num_format": "#,##0"}),
        "totv2": wb.add_format({"bold": True, "bg_color": "#c6d9f1", "border": 1,
                                  "align": "right", "num_format": "#,##0.00"}),
        "totd":  wb.add_format({"bold": True, "bg_color": "#c6d9f1", "border": 1,
                                  "align": "right", "num_format": "#,##0", "font_color": "#C00000"}),
        "grand": wb.add_format({"bold": True, "bg_color": "#1e3a5f", "font_color": "#FFFFFF",
                                  "border": 1}),
        "grandv":wb.add_format({"bold": True, "bg_color": "#1e3a5f", "font_color": "#FFFFFF",
                                  "border": 1, "align": "right", "num_format": "#,##0"}),
        "grandv2":wb.add_format({"bold": True,"bg_color": "#1e3a5f","font_color": "#FFFFFF",
                                  "border": 1, "align": "right", "num_format": "#,##0.00"}),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Report 1 — Full Reconciliation Summary
# ─────────────────────────────────────────────────────────────────────────────

def write_full_summary(api_df, results, start_date, end_date):
    """
    Single-table summary:
    API | Orchestrator | Diff(API-Orch) | PSP Reconciled | Diff(API-PSP)  [Orders]
    API | Orchestrator | Diff(API-Orch) | PSP Reconciled | Diff(API-PSP)  [Revenue]
    """
    stats = compute_summary_stats(api_df, results)
    if not stats:
        return None

    buf = io.BytesIO()
    wb  = xlsxwriter.Workbook(buf, {"nan_inf_to_errors": True})
    f   = _wb_formats(wb)
    ws  = wb.add_worksheet("Full Summary")

    ws.set_column("A:A", 22)
    for i in range(1, 11):
        ws.set_column(i, i, 18)

    # Row 0 — Title
    ws.merge_range("A1:K1",
        f"Revenue Reconciliation Summary  |  {start_date} → {end_date}", f["title"])

    # Row 1 — Group headers
    ws.write("A2", "", f["hdr1"])
    ws.merge_range("B2:F2", "Orders", f["hdr1"])
    ws.merge_range("G2:K2", "Revenue (USD)", f["hdr1"])

    # Row 2 — Column headers
    col_heads = ["Metric",
                 "API", "Orchestrator", "Diff (API-Orch)", "PSP Reconciled", "Diff (API-PSP)",
                 "API", "Orchestrator", "Diff (API-Orch)", "PSP Reconciled", "Diff (API-PSP)"]
    for ci, h in enumerate(col_heads):
        ws.write(2, ci, h, f["hdr2"])

    # Row 3 — Data
    row_data = [
        "Full Period",
        stats["api_orders"],  stats["orch_orders"],  stats["diff_orch"],
        stats["psp_orders"],  stats["diff_psp"],
        stats["api_rev"],     stats["orch_rev"],      stats["diff_orch_rev"],
        stats["psp_rev"],     stats["diff_psp_rev"],
    ]
    fmts = [f["name"],
            f["val"], f["val"], f["diff"], f["val"], f["diff"],
            f["val2"],f["val2"],f["diff2"],f["val2"],f["diff2"]]
    for ci, (v, fmt) in enumerate(zip(row_data, fmts)):
        ws.write(3, ci, v, fmt)

    # Also show per-bank Phase 1 breakdown below
    ri = 5
    if "combined" in results:
        ws.write(5, 0, "Phase 1 — Bank Breakdown", f["hdr1"])
        for ci, h in enumerate(["Bank", "API Rows", "Reconciled", "Mismatch", "Not In Bank", "Match %"]):
            ws.write(6, ci, h, f["hdr2"])
        combined = results["combined"]
        ri = 7
        for bank in combined.get("Bank", pd.Series()).dropna().unique():
            sub = combined[combined["Bank"] == bank]
            rec = (sub["Verdict"] == "RECONCILED").sum()
            mis = (sub["Verdict"] == "AMOUNT MISMATCH").sum()
            nib = (sub["Verdict"] == "NOT IN BANK").sum()
            pct = rec / len(sub) * 100 if len(sub) else 0
            ws.write(ri, 0, bank,        f["name"])
            ws.write(ri, 1, len(sub),    f["val"])
            ws.write(ri, 2, int(rec),    f["val"])
            ws.write(ri, 3, int(mis),    f["diff"])
            ws.write(ri, 4, int(nib),    f["diff"])
            ws.write(ri, 5, f"{pct:.1f}%", f["name"])
            ri += 1

    # Phase 2 PSP breakdown
    if results.get("phase2"):
        ri += 1
        ws.write(ri, 0, "Phase 2 — PSP Breakdown", f["hdr1"])
        ri += 1
        for ci, h in enumerate(["PSP", "PSP Rows", "Reconciled", "Mismatch", "Not In Orch", "Match %"]):
            ws.write(ri, ci, h, f["hdr2"])
        ri += 1
        for psp_key, df2 in results["phase2"].items():
            if not isinstance(df2, pd.DataFrame) or df2.empty: continue
            rec = (df2["Verdict"] == "RECONCILED").sum()
            mis = (df2["Verdict"] == "AMOUNT MISMATCH").sum()
            nio = (df2["Verdict"] == "NOT IN ORCH").sum()
            pct = rec / len(df2) * 100 if len(df2) else 0
            ws.write(ri, 0, psp_key.replace("_"," ").title(), f["name"])
            ws.write(ri, 1, len(df2),   f["val"])
            ws.write(ri, 2, int(rec),   f["val"])
            ws.write(ri, 3, int(mis),   f["diff"])
            ws.write(ri, 4, int(nio),   f["diff"])
            ws.write(ri, 5, f"{pct:.1f}%", f["name"])
            ri += 1

    # ── Add Order Wise + Amount Wise sheets ──────────────────────────────────
    try:
        data = build_order_wise(api_df, results, results.get("phase2", {}))
        if data:
            rows_ow  = data["rows"]
            p1_label = data["p1_label"]
            p2_label = data["p2_label"]

            for sheet_name, use_rev in [("Order Wise", False), ("Amount Wise", True)]:
                ws2 = wb.add_worksheet(sheet_name)
                ws2.freeze_panes(4, 2)
                ws2.set_column("A:A", 26)
                ws2.set_column("B:B", 10)
                for ci in range(2, 12):
                    ws2.set_column(ci, ci, 16)

                sfx   = "rev" if use_rev else "n"
                d_sfx = "_rev" if use_rev else ""
                lbl   = "Revenue (USD)" if use_rev else "Orders"
                vfmt  = f["val2"] if use_rev else f["val"]
                dfmt  = f["diff2"] if use_rev else f["diff"]

                ws2.merge_range(0, 0, 0, 11,
                    f"Revenue Reconciliation — {lbl}  |  {p1_label} vs {p2_label}", f["title"])
                ws2.merge_range(1, 0, 1, 1, "Package / Plan", f["hdr1"])
                ws2.merge_range(1, 2, 1, 6, p1_label, f["hdr1"])
                ws2.merge_range(1, 7, 1, 11, p2_label, f["hdr1"])
                for ci, lh in enumerate(["", "", "API", "Orchestrator", "Diff (API-Orch)",
                                          "PSP Reconciled", "Diff (API-PSP)"] +
                                         ["API", "Orchestrator", "Diff (API-Orch)",
                                          "PSP Reconciled", "Diff (API-PSP)"]):
                    ws2.write(2, ci, lh if ci > 1 else ("Package" if ci==0 else "Plan"), f["hdr2"])
                for ci in range(12):
                    ws2.write(3, ci, lbl if ci >= 2 else ("Package" if ci==0 else "Plan"), f["hdr2"])

                ri2 = 4
                prev_pkg2 = None
                for r in rows_ow:
                    pkg = r["Package"]; plan = r["Plan"]
                    is_tot = r["_is_total"]; is_grand = pkg == "GRAND TOTAL"

                    if not is_tot and not is_grand and pkg != prev_pkg2:
                        if pkg in FUTURES_PACKAGES and prev_pkg2 is None:
                            ws2.merge_range(ri2, 0, ri2, 11, "── FUTURES ──", f["pkg"])
                            ri2 += 1
                        elif pkg in CFD_PACKAGES and (prev_pkg2 is None or prev_pkg2 not in CFD_PACKAGES):
                            ws2.merge_range(ri2, 0, ri2, 11, "── CFD ──", f["pkg"])
                            ri2 += 1
                        prev_pkg2 = pkg

                    nf2 = f["grand"] if is_grand else (f["tot"] if is_tot else f["name"])
                    vf2 = f["grand"] if is_grand else (f["tot"] if is_tot else vfmt)
                    df2 = f["grand"] if is_grand else (f["tot"] if is_tot else dfmt)

                    vals = [
                        pkg, plan,
                        r.get(f"P1_api_{sfx}", 0),
                        r.get(f"P1_orch_{sfx}", 0),
                        r.get(f"P1_diff_orch{d_sfx}", 0),
                        r.get(f"P1_psp_{sfx}", 0),
                        r.get(f"P1_diff_psp{d_sfx}", 0),
                        r.get(f"P2_api_{sfx}", 0),
                        r.get(f"P2_orch_{sfx}", 0),
                        r.get(f"P2_diff_orch{d_sfx}", 0),
                        r.get(f"P2_psp_{sfx}", 0),
                        r.get(f"P2_diff_psp{d_sfx}", 0),
                    ]
                    ws2.write(ri2, 0, vals[0], nf2)
                    ws2.write(ri2, 1, vals[1], nf2)
                    for ci in range(2, 12):
                        is_diff = ci in (4, 6, 9, 11)
                        ws2.write(ri2, ci, vals[ci], df2 if is_diff else vf2)
                    ri2 += 1
    except Exception as _e:
        pass  # Order/Amount wise sheets are optional

    wb.close()
    buf.seek(0)
    return buf


# ─────────────────────────────────────────────────────────────────────────────
# Report 2 — Comparison Summary (Package/Plan × Period)
# ─────────────────────────────────────────────────────────────────────────────

def write_comparison_summary(api_df, results):
    """
    Side-by-side package/plan breakdown for two periods.
    Format matches the previous HTML/Excel report exactly.
    Two sub-tables: Orders and Revenue.
    """
    data = build_order_wise(api_df, results, results.get("phase2", {}))
    if not data:
        return None

    rows     = data["rows"]
    p1_label = data["p1_label"]
    p2_label = data["p2_label"]

    buf = io.BytesIO()
    wb  = xlsxwriter.Workbook(buf, {"nan_inf_to_errors": True})
    f   = _wb_formats(wb)

    for sheet_name, use_rev in [("Order Wise", False), ("Amount Wise", True)]:
        ws = wb.add_worksheet(sheet_name)
        ws.freeze_panes(4, 2)
        ws.set_column("A:A", 26)
        ws.set_column("B:B", 10)
        for ci in range(2, 12):
            ws.set_column(ci, ci, 16)

        sfx   = "_rev" if use_rev else "_n"
        d_sfx = "_rev" if use_rev else ""
        lbl   = "Revenue (USD)" if use_rev else "Orders"
        vfmt  = f["val2"] if use_rev else f["val"]
        dfmt  = f["diff2"] if use_rev else f["diff"]
        tvfmt = f["totv2"] if use_rev else f["totv"]
        tdfmt = f["totd"]
        gvfmt = f["grandv2"] if use_rev else f["grandv"]
        gf    = f["grand"]

        # Row 0 — title
        ws.merge_range(0, 0, 0, 11,
            f"Revenue Reconciliation — {lbl}  |  {p1_label} vs {p2_label}", f["title"])

        # Row 1 — period headers
        ws.merge_range(1, 0, 1, 1, "Package / Plan", f["hdr1"])
        ws.merge_range(1, 2, 1, 6, p1_label,         f["hdr1"])
        ws.merge_range(1, 7, 1,11, p2_label,          f["hdr1"])

        # Row 2 — sub-group headers
        ws.write(2, 0, "",              f["hdr2"])
        ws.write(2, 1, "",              f["hdr2"])
        for ci, lh in enumerate(["API", "Orchestrator", "Diff (API-Orch)",
                                  "PSP Reconciled", "Diff (API-PSP)"] * 2):
            ws.write(2, ci + 2, lh, f["hdr2"])

        # Row 3 — metric label row
        for ci in range(12):
            ws.write(3, ci, lbl if ci in (2,3,4,5,6,7,8,9,10,11) else
                    ("Package" if ci==0 else "Plan"), f["hdr3"])

        ri = 4
        prev_pkg = None
        for r in rows:
            pkg     = r["Package"]
            plan    = r["Plan"]
            is_tot  = r["_is_total"]
            is_grand= pkg == "GRAND TOTAL"

            # Section divider
            if not is_tot and not is_grand and pkg != prev_pkg:
                section = ""
                if pkg == FUTURES_PACKAGES[0] and prev_pkg is None:
                    section = "── FUTURES ──"
                elif pkg == CFD_PACKAGES[0]:
                    section = "── CFD ──"
                if section:
                    ws.merge_range(ri, 0, ri, 11, section, f["pkg"])
                    ri += 1
                prev_pkg = pkg

            nf = gf   if is_grand else (f["tot"]  if is_tot else f["name"])
            vf = gvfmt if is_grand else (tvfmt     if is_tot else vfmt)
            df = gvfmt if is_grand else (tdfmt     if is_tot else dfmt)

            p1a = r[f"P1_api{sfx}"]
            p1o = r[f"P1_orch{sfx}"]
            p1do= r[f"P1_diff_orch{d_sfx}" if d_sfx else "P1_diff_orch"]
            p1p = r[f"P1_psp{sfx}"]
            p1dp= r[f"P1_diff_psp{d_sfx}"  if d_sfx else "P1_diff_psp"]
            p2a = r[f"P2_api{sfx}"]
            p2o = r[f"P2_orch{sfx}"]
            p2do= r[f"P2_diff_orch{d_sfx}" if d_sfx else "P2_diff_orch"]
            p2p = r[f"P2_psp{sfx}"]
            p2dp= r[f"P2_diff_psp{d_sfx}"  if d_sfx else "P2_diff_psp"]

            ws.write(ri, 0, pkg,   nf)
            ws.write(ri, 1, plan,  nf)
            for ci, (v, fmt) in enumerate(zip(
                [p1a, p1o, p1do, p1p, p1dp, p2a, p2o, p2do, p2p, p2dp],
                [vf,  vf,  df,   vf,  df,   vf,  vf,  df,   vf,  df ]
            )):
                ws.write(ri, ci + 2, v, fmt)
            ri += 1

    # ── Add Order Wise + Amount Wise sheets ──────────────────────────────────
    try:
        data = build_order_wise(api_df, results, results.get("phase2", {}))
        if data:
            rows_ow  = data["rows"]
            p1_label = data["p1_label"]
            p2_label = data["p2_label"]

            for sheet_name, use_rev in [("Order Wise", False), ("Amount Wise", True)]:
                ws2 = wb.add_worksheet(sheet_name)
                ws2.freeze_panes(4, 2)
                ws2.set_column("A:A", 26)
                ws2.set_column("B:B", 10)
                for ci in range(2, 12):
                    ws2.set_column(ci, ci, 16)

                sfx   = "rev" if use_rev else "n"
                d_sfx = "_rev" if use_rev else ""
                lbl   = "Revenue (USD)" if use_rev else "Orders"
                vfmt  = f["val2"] if use_rev else f["val"]
                dfmt  = f["diff2"] if use_rev else f["diff"]

                ws2.merge_range(0, 0, 0, 11,
                    f"Revenue Reconciliation — {lbl}  |  {p1_label} vs {p2_label}", f["title"])
                ws2.merge_range(1, 0, 1, 1, "Package / Plan", f["hdr1"])
                ws2.merge_range(1, 2, 1, 6, p1_label, f["hdr1"])
                ws2.merge_range(1, 7, 1, 11, p2_label, f["hdr1"])
                for ci, lh in enumerate(["", "", "API", "Orchestrator", "Diff (API-Orch)",
                                          "PSP Reconciled", "Diff (API-PSP)"] +
                                         ["API", "Orchestrator", "Diff (API-Orch)",
                                          "PSP Reconciled", "Diff (API-PSP)"]):
                    ws2.write(2, ci, lh if ci > 1 else ("Package" if ci==0 else "Plan"), f["hdr2"])
                for ci in range(12):
                    ws2.write(3, ci, lbl if ci >= 2 else ("Package" if ci==0 else "Plan"), f["hdr2"])

                ri2 = 4
                prev_pkg2 = None
                for r in rows_ow:
                    pkg = r["Package"]; plan = r["Plan"]
                    is_tot = r["_is_total"]; is_grand = pkg == "GRAND TOTAL"

                    if not is_tot and not is_grand and pkg != prev_pkg2:
                        if pkg in FUTURES_PACKAGES and prev_pkg2 is None:
                            ws2.merge_range(ri2, 0, ri2, 11, "── FUTURES ──", f["pkg"])
                            ri2 += 1
                        elif pkg in CFD_PACKAGES and (prev_pkg2 is None or prev_pkg2 not in CFD_PACKAGES):
                            ws2.merge_range(ri2, 0, ri2, 11, "── CFD ──", f["pkg"])
                            ri2 += 1
                        prev_pkg2 = pkg

                    nf2 = f["grand"] if is_grand else (f["tot"] if is_tot else f["name"])
                    vf2 = f["grand"] if is_grand else (f["tot"] if is_tot else vfmt)
                    df2 = f["grand"] if is_grand else (f["tot"] if is_tot else dfmt)

                    vals = [
                        pkg, plan,
                        r.get(f"P1_api_{sfx}", 0),
                        r.get(f"P1_orch_{sfx}", 0),
                        r.get(f"P1_diff_orch{d_sfx}", 0),
                        r.get(f"P1_psp_{sfx}", 0),
                        r.get(f"P1_diff_psp{d_sfx}", 0),
                        r.get(f"P2_api_{sfx}", 0),
                        r.get(f"P2_orch_{sfx}", 0),
                        r.get(f"P2_diff_orch{d_sfx}", 0),
                        r.get(f"P2_psp_{sfx}", 0),
                        r.get(f"P2_diff_psp{d_sfx}", 0),
                    ]
                    ws2.write(ri2, 0, vals[0], nf2)
                    ws2.write(ri2, 1, vals[1], nf2)
                    for ci in range(2, 12):
                        is_diff = ci in (4, 6, 9, 11)
                        ws2.write(ri2, ci, vals[ci], df2 if is_diff else vf2)
                    ri2 += 1
    except Exception as _e:
        pass  # Order/Amount wise sheets are optional

    wb.close()
    buf.seek(0)
    return buf


# ─────────────────────────────────────────────────────────────────────────────
# Report 2 — Comparison Summary (Package/Plan × Period)
# ─────────────────────────────────────────────────────────────────────────────
