"""
Reconciliation Gap Report
─────────────────────────
Sheet 1: Diff (Orch) — API orders NOT found in any orchestrator/bank
Sheet 2: Diff (PSP)  — API orders found in orch but NOT confirmed by any PSP
Sheet 3: Summary     — Aggregated counts, amounts, and root causes
"""
import io, pandas as pd
from engine.loader import find_col, to_numeric_col
from engine.report_order_wise import _build_orch_set, _build_psp_set


def build_recon_gap_report(api_df, results, start_date, end_date):
    """Generate the 3-sheet reconciliation gap Excel report."""

    buf = io.BytesIO()

    # ── Identify columns ──────────────────────────────────────────────────
    tid_col = find_col(api_df, ["Transaction ID", "TransactionID"])
    trk_col = find_col(api_df, ["Tracking ID", "TrackingID"])
    gt_col  = find_col(api_df, ["Grand Total", "GrandTotal"])
    pt_col  = find_col(api_df, ["Plan Type", "PlanType"])
    ca_col  = find_col(api_df, ["Created At", "CreatedAt"])
    st_col  = find_col(api_df, ["Status", "status"])
    oi_col  = find_col(api_df, ["Order ID", "OrderID"])
    em_col  = find_col(api_df, ["Customer Email", "CustomerEmail"])
    gw_col  = find_col(api_df, ["Gateway", "gateway"])

    if not tid_col or not gt_col:
        return None

    api = api_df.copy()
    api["_gt"] = to_numeric_col(api[gt_col].astype(str))

    # ── Build ID sets ─────────────────────────────────────────────────────
    orch_ids = _build_orch_set(results)
    psp_ids  = _build_psp_set(results, results.get("phase2", {}))
    all_tids = set(api[tid_col].dropna().astype(str))

    # ── Classify each API order ───────────────────────────────────────────
    def _classify(row):
        tid = str(row.get(tid_col, ""))
        trk = str(row.get(trk_col, "")) if trk_col else ""
        in_orch = tid in orch_ids or trk in orch_ids
        in_psp  = tid in psp_ids or trk in psp_ids
        if in_orch and in_psp:
            return "FULLY RECONCILED"
        elif in_orch and not in_psp:
            return "IN ORCH, NOT IN PSP"
        else:
            return "NOT IN ORCH"

    api["_class"] = api.apply(_classify, axis=1)

    # ── Detect prefix / gateway ───────────────────────────────────────────
    def _detect_prefix(tid):
        tid = str(tid)
        if tid.startswith("BP_"):  return "Bridgerpay"
        if tid.startswith("PP_"):  return "Payprocc"
        if tid.startswith("ZP_"):  return "ZEN"
        if tid.startswith("OP-"):  return "TC Pay"
        if tid.startswith("TC-"):  return "TC Pay"
        return "Unknown"

    def _detect_prefix_trk(trk):
        trk = str(trk)
        if trk.startswith("B2B_"): return "Coinsbuy"
        if trk.startswith("CFM_"): return "Confirmo"
        return None

    api["_orch_name"] = api[tid_col].apply(_detect_prefix)
    if trk_col:
        trk_name = api[trk_col].apply(_detect_prefix_trk)
        mask = trk_name.notna()
        api.loc[mask, "_orch_name"] = trk_name[mask]

    # ── Root cause assignment ─────────────────────────────────────────────
    def _root_cause(row):
        cls = row["_class"]
        orch = row["_orch_name"]
        if cls == "FULLY RECONCILED":
            return "Fully reconciled — no action needed"
        elif cls == "NOT IN ORCH":
            if orch == "Unknown":
                return "Unknown gateway prefix — Transaction ID does not match any known bank pattern (BP_, PP_, ZP_, B2B_, CFM_, TC-)"
            else:
                return f"Not found in {orch} statement — order may be pending, refunded, or failed at gateway"
        else:  # IN ORCH, NOT IN PSP
            if orch in ("Coinsbuy", "ZEN", "Confirmo", "TC Pay"):
                return f"Independent PSP ({orch}) — should be PSP-confirmed; verify Phase 1 match"
            elif orch == "Bridgerpay":
                return "Found in Bridgerpay but no downstream PSP confirmed settlement — check Nuvei/Axcess/Paysafe/PayPal/Unlimit/Trust Payment/Payabl"
            elif orch == "Payprocc":
                return "Found in Payprocc but no downstream PSP confirmed settlement — check Paysafe(PP)/DLocal/Skrill"
            else:
                return "In orchestrator but PSP settlement not confirmed"

    api["_root_cause"] = api.apply(_root_cause, axis=1)

    # ── Remarks ───────────────────────────────────────────────────────────
    def _remarks(row):
        cls = row["_class"]
        orch = row["_orch_name"]
        gt = row["_gt"]
        if cls == "FULLY RECONCILED":
            return "OK"
        elif cls == "NOT IN ORCH":
            if orch == "Unknown":
                return f"${gt:,.2f} unreconciled — no matching bank prefix"
            return f"${gt:,.2f} missing from {orch}"
        else:
            return f"${gt:,.2f} in {orch} but not PSP-confirmed"

    api["_remarks"] = api.apply(_remarks, axis=1)

    # ── Build output columns ──────────────────────────────────────────────
    out_cols = []
    col_map = {}
    for label, src in [
        ("Transaction ID", tid_col),
        ("Tracking ID", trk_col),
        ("Order ID", oi_col),
        ("Grand Total (USD)", "_gt"),
        ("Plan Type", pt_col),
        ("Created At", ca_col),
        ("Customer Email", em_col),
        ("Gateway", gw_col),
        ("API Status", st_col),
        ("Orchestrator", "_orch_name"),
        ("Root Cause", "_root_cause"),
        ("Remarks", "_remarks"),
        ("Final Verdict", "_class"),
    ]:
        if src and src in api.columns:
            out_cols.append(label)
            col_map[label] = src

    def _build_sheet(df):
        out = pd.DataFrame()
        for label, src in col_map.items():
            out[label] = df[src].values
        return out

    # ── Sheet 1: Diff (Orch) — NOT IN ORCH ────────────────────────────────
    diff_orch = api[api["_class"] == "NOT IN ORCH"].copy()
    diff_orch = diff_orch.sort_values("_gt", ascending=False)
    sheet1 = _build_sheet(diff_orch)

    # ── Sheet 2: Diff (PSP) — IN ORCH, NOT IN PSP ─────────────────────────
    diff_psp = api[api["_class"] == "IN ORCH, NOT IN PSP"].copy()
    diff_psp = diff_psp.sort_values("_gt", ascending=False)
    sheet2 = _build_sheet(diff_psp)

    # ── Sheet 3: Summary ──────────────────────────────────────────────────
    summary_rows = []

    # Overall stats
    total_orders = len(api)
    total_rev    = api["_gt"].sum()
    reconciled   = api[api["_class"] == "FULLY RECONCILED"]
    not_orch     = api[api["_class"] == "NOT IN ORCH"]
    not_psp      = api[api["_class"] == "IN ORCH, NOT IN PSP"]

    summary_rows.append({
        "Category": "TOTAL API ORDERS",
        "Orders": total_orders,
        "Amount (USD)": round(total_rev, 2),
        "% of Total": "100.0%",
        "Root Cause": ""
    })
    summary_rows.append({
        "Category": "Fully Reconciled (Orch + PSP)",
        "Orders": len(reconciled),
        "Amount (USD)": round(reconciled["_gt"].sum(), 2),
        "% of Total": f"{len(reconciled)/max(total_orders,1)*100:.1f}%",
        "Root Cause": "No action needed"
    })
    summary_rows.append({
        "Category": "Diff (Orch) — Not in any Bank",
        "Orders": len(not_orch),
        "Amount (USD)": round(not_orch["_gt"].sum(), 2),
        "% of Total": f"{len(not_orch)/max(total_orders,1)*100:.1f}%",
        "Root Cause": "API orders with no matching bank/orchestrator record"
    })
    summary_rows.append({
        "Category": "Diff (PSP) — In Orch, Not in PSP",
        "Orders": len(not_psp),
        "Amount (USD)": round(not_psp["_gt"].sum(), 2),
        "% of Total": f"{len(not_psp)/max(total_orders,1)*100:.1f}%",
        "Root Cause": "Orchestrator confirmed but PSP settlement not verified"
    })

    # Breakdown by orchestrator for NOT IN ORCH
    summary_rows.append({"Category": "", "Orders": "", "Amount (USD)": "", "% of Total": "", "Root Cause": ""})
    summary_rows.append({"Category": "── DIFF (ORCH) BREAKDOWN BY PREFIX ──",
                          "Orders": "", "Amount (USD)": "", "% of Total": "", "Root Cause": ""})
    for orch_name in sorted(not_orch["_orch_name"].unique()):
        sub = not_orch[not_orch["_orch_name"] == orch_name]
        cause = "Unknown prefix — no bank mapping" if orch_name == "Unknown" else f"Not found in {orch_name} statement"
        summary_rows.append({
            "Category": f"  {orch_name}",
            "Orders": len(sub),
            "Amount (USD)": round(sub["_gt"].sum(), 2),
            "% of Total": f"{len(sub)/max(total_orders,1)*100:.1f}%",
            "Root Cause": cause
        })

    # Breakdown by orchestrator for IN ORCH NOT IN PSP
    summary_rows.append({"Category": "", "Orders": "", "Amount (USD)": "", "% of Total": "", "Root Cause": ""})
    summary_rows.append({"Category": "── DIFF (PSP) BREAKDOWN BY ORCHESTRATOR ──",
                          "Orders": "", "Amount (USD)": "", "% of Total": "", "Root Cause": ""})
    for orch_name in sorted(not_psp["_orch_name"].unique()):
        sub = not_psp[not_psp["_orch_name"] == orch_name]
        if orch_name == "Bridgerpay":
            cause = "Check Nuvei NI/AQ, Axcess, Trust Payment, Payabl, Paysafe(BP), PayPal, Unlimit, Confirmo(BP)"
        elif orch_name == "Payprocc":
            cause = "Check Paysafe(PP), DLocal, Skrill"
        else:
            cause = f"Independent PSP {orch_name} — verify Phase 1 match status"
        summary_rows.append({
            "Category": f"  {orch_name}",
            "Orders": len(sub),
            "Amount (USD)": round(sub["_gt"].sum(), 2),
            "% of Total": f"{len(sub)/max(total_orders,1)*100:.1f}%",
            "Root Cause": cause
        })

    sheet3 = pd.DataFrame(summary_rows)

    # ── Write Excel ───────────────────────────────────────────────────────
    try:
        import xlsxwriter
        wb = xlsxwriter.Workbook(buf, {"in_memory": True})

        # Formats
        f_title = wb.add_format({"bold": True, "font_size": 14, "bg_color": "#0a1628",
                                  "font_color": "#ffffff", "border": 1})
        f_hdr   = wb.add_format({"bold": True, "font_size": 11, "bg_color": "#1e3a6b",
                                  "font_color": "#ffffff", "border": 1, "text_wrap": True})
        f_money = wb.add_format({"num_format": "$#,##0.00", "border": 1})
        f_text  = wb.add_format({"border": 1, "text_wrap": True})
        f_verdict_ok = wb.add_format({"border": 1, "bg_color": "#e8f5e9", "font_color": "#1b5e20", "bold": True})
        f_verdict_bad = wb.add_format({"border": 1, "bg_color": "#ffebee", "font_color": "#e53935", "bold": True})
        f_section = wb.add_format({"bold": True, "font_size": 12, "bg_color": "#f5a623",
                                    "font_color": "#0a1628", "border": 1})

        # ── Sheet 1: Diff (Orch) ──────────────────────────────────────────
        ws1 = wb.add_worksheet("Diff (Orch) vs API")
        ws1.freeze_panes(2, 0)
        title = f"Diff (Orch) — API Orders Not Found in Any Bank  |  {start_date} → {end_date}  |  {len(sheet1):,} orders  |  ${diff_orch['_gt'].sum():,.2f}"
        ws1.merge_range(0, 0, 0, len(sheet1.columns)-1, title, f_title)
        for ci, col in enumerate(sheet1.columns):
            ws1.write(1, ci, col, f_hdr)
            ws1.set_column(ci, ci, max(14, len(col)+4))
        ws1.set_column(len(sheet1.columns)-3, len(sheet1.columns)-1, 40)  # wider for Root Cause/Remarks

        for ri, (_, row) in enumerate(sheet1.iterrows(), start=2):
            for ci, col in enumerate(sheet1.columns):
                val = row[col]
                if col == "Grand Total (USD)" and isinstance(val, (int, float)):
                    ws1.write(ri, ci, val, f_money)
                elif col == "Final Verdict":
                    fmt = f_verdict_ok if val == "FULLY RECONCILED" else f_verdict_bad
                    ws1.write(ri, ci, val, fmt)
                else:
                    ws1.write(ri, ci, str(val) if pd.notna(val) else "", f_text)

        # ── Sheet 2: Diff (PSP) ──────────────────────────────────────────
        ws2 = wb.add_worksheet("Diff (PSP) vs Orch vs API")
        ws2.freeze_panes(2, 0)
        title2 = f"Diff (PSP) — In Orchestrator But Not PSP-Confirmed  |  {start_date} → {end_date}  |  {len(sheet2):,} orders  |  ${diff_psp['_gt'].sum():,.2f}"
        ws2.merge_range(0, 0, 0, len(sheet2.columns)-1, title2, f_title)
        for ci, col in enumerate(sheet2.columns):
            ws2.write(1, ci, col, f_hdr)
            ws2.set_column(ci, ci, max(14, len(col)+4))
        ws2.set_column(len(sheet2.columns)-3, len(sheet2.columns)-1, 40)

        for ri, (_, row) in enumerate(sheet2.iterrows(), start=2):
            for ci, col in enumerate(sheet2.columns):
                val = row[col]
                if col == "Grand Total (USD)" and isinstance(val, (int, float)):
                    ws2.write(ri, ci, val, f_money)
                elif col == "Final Verdict":
                    ws2.write(ri, ci, val, f_verdict_bad)
                else:
                    ws2.write(ri, ci, str(val) if pd.notna(val) else "", f_text)

        # ── Sheet 3: Summary ──────────────────────────────────────────────
        ws3 = wb.add_worksheet("Summary & Root Causes")
        ws3.freeze_panes(2, 0)
        ws3.merge_range(0, 0, 0, 4,
            f"Reconciliation Gap Summary  |  {start_date} → {end_date}", f_title)
        for ci, col in enumerate(sheet3.columns):
            ws3.write(1, ci, col, f_hdr)
        ws3.set_column(0, 0, 40)
        ws3.set_column(1, 1, 12)
        ws3.set_column(2, 2, 18)
        ws3.set_column(3, 3, 12)
        ws3.set_column(4, 4, 70)

        for ri, (_, row) in enumerate(sheet3.iterrows(), start=2):
            cat = str(row["Category"])
            is_section = cat.startswith("──")
            is_total = cat == "TOTAL API ORDERS"
            for ci, col in enumerate(sheet3.columns):
                val = row[col]
                if is_section:
                    ws3.write(ri, ci, str(val) if pd.notna(val) and val != "" else "", f_section)
                elif col == "Amount (USD)" and isinstance(val, (int, float)):
                    ws3.write(ri, ci, val, f_money)
                elif is_total:
                    ws3.write(ri, ci, str(val) if pd.notna(val) else "",
                              wb.add_format({"bold": True, "border": 1, "bg_color": "#e8ecf4"}))
                else:
                    ws3.write(ri, ci, str(val) if pd.notna(val) and val != "" else "", f_text)

        wb.close()
    except Exception:
        # Fallback to pandas ExcelWriter
        with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
            sheet1.to_excel(writer, sheet_name="Diff (Orch) vs API", index=False)
            sheet2.to_excel(writer, sheet_name="Diff (PSP) vs Orch vs API", index=False)
            sheet3.to_excel(writer, sheet_name="Summary & Root Causes", index=False)

    buf.seek(0)
    return buf
