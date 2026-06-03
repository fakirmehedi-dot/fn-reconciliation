"""
engine/report_mismatch.py
Amount Mismatch Report: Futures + CFD sheets with full audit columns.
"""
import pandas as pd, numpy as np, io, xlsxwriter
from engine.loader import find_col, to_numeric_col
from engine.verdict import enrich_combined, VERDICTS

FUTURES_PKGS = ["Futures Bolt","Futures Legacy","Futures Rapid"]

MISMATCH_COLS = [
    "Order ID","Customer Email","Account","Account Type",
    "Plan Name","Plan Type","Order Type","Gateway",
    "Transaction ID","Grand Total","Status","Created At","Updated At",
    "Bank","Bank_Amount","Diff (USD)","Auto Verdict","Root Cause",
]

BRAND = {"bg":"#0a1628","accent":"#f5a623","white":"#ffffff","red":"#e53935","green":"#00c853"}

def _wb_fmts(wb):
    f = lambda **kw: wb.add_format(kw)
    return {
        "title": f(bold=True,bg_color=BRAND["bg"],font_color=BRAND["accent"],
                   font_size=13,align="center",valign="vcenter"),
        "hdr":   f(bold=True,bg_color="#1a3a6b",font_color=BRAND["white"],
                   align="center",valign="vcenter",border=1,text_wrap=True,font_size=10),
        "ok":    f(bg_color="#e8f5e9",border=1,font_size=10),
        "mis":   f(bg_color="#fff8e1",border=1,font_size=10),
        "nib":   f(bg_color="#ffebee",border=1,font_size=10),
        "cell":  f(border=1,font_size=10),
        "num":   f(border=1,num_format="#,##0.00",align="right",font_size=10),
        "red":   f(border=1,num_format="#,##0.00",align="right",
                   font_color=BRAND["red"],font_size=10),
        "tot":   f(bold=True,bg_color=BRAND["bg"],font_color=BRAND["white"],border=1),
        "tot_n": f(bold=True,bg_color=BRAND["bg"],font_color=BRAND["white"],
                   border=1,num_format="#,##0.00",align="right"),
    }

def _available(df, cols):
    return [c for c in cols if c in df.columns]

def _write_sheet(ws, df, fmts, title):
    ws.set_row(0, 26)
    ws.set_column("A:A", 14)
    ws.set_column("B:B", 22)
    ws.set_column("C:T", 16)

    cols = _available(df, MISMATCH_COLS)
    ws.merge_range(0, 0, 0, len(cols)-1, title, fmts["title"])

    for ci, c in enumerate(cols):
        ws.write(1, ci, c, fmts["hdr"])

    for ri, (_, row) in enumerate(df[cols].iterrows(), start=2):
        v = str(row.get("Auto Verdict",""))
        row_fmt = (fmts["ok"]  if v == VERDICTS["FULLY_RECONCILED"] else
                   fmts["mis"] if "Mismatch" in v else
                   fmts["nib"])
        for ci, col in enumerate(cols):
            val = row[col]
            fmt = fmts["num"] if col in ("Grand Total","Bank_Amount","Diff (USD)") else row_fmt
            ws.write(ri, ci, val, fmt)


def write_mismatch_excel(results, api_df, start_date, end_date):
    """Write Amount Mismatch Report: Futures + CFD sheets."""
    combined = results.get("combined", pd.DataFrame())
    if combined.empty:
        return None

    combined = enrich_combined(combined)

    # Filter to only non-fully-reconciled rows
    mis = combined[combined["Auto Verdict"] != VERDICTS["FULLY_RECONCILED"]].copy()
    if mis.empty:
        mis = combined.copy()  # Show all if nothing to report

    # Attach API columns that may be missing
    gt_col  = find_col(api_df, ["Grand Total","GrandTotal"])
    tid_col = find_col(api_df, ["Transaction ID","TransactionID"])
    tid_api_col = find_col(combined, ["Transaction ID","TransactionID"])

    plan_col = find_col(api_df, ["Plan Type","PlanType"])
    if plan_col and tid_col and tid_api_col:
        plan_map = dict(zip(api_df[tid_col], api_df[plan_col]))
        mis["_plan_type"] = mis[tid_api_col].map(plan_map)
    else:
        mis["_plan_type"] = "Unknown"

    # Split Futures vs CFD
    def is_futures(pt):
        pt = str(pt).lower()
        return any(x in pt for x in ["futures bolt","futures legacy","futures rapid"])

    futures_mask = mis["_plan_type"].apply(is_futures)
    df_futures = mis[futures_mask].copy()
    df_cfd     = mis[~futures_mask].copy()

    buf = io.BytesIO()
    wb  = xlsxwriter.Workbook(buf, {"nan_inf_to_errors": True})
    fmts= _wb_fmts(wb)

    for sheet_name, df_sheet in [("Futures", df_futures), ("CFD", df_cfd)]:
        ws = wb.add_worksheet(sheet_name)
        ws.freeze_panes(2, 0)
        title = (f"FundedNext  ·  Amount Mismatch Report  ·  {sheet_name}"
                 f"  |  {start_date} → {end_date}")
        _write_sheet(ws, df_sheet, fmts, title)

    wb.close()
    buf.seek(0)
    return buf
