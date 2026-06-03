"""
engine/writer.py  –  Generate output files (XLSX / CSV)
"""
import io
import pandas as pd
import xlsxwriter

# Colour palette
C = {
    "header_bg": "#1a4fd6", "header_fg": "#ffffff",
    "recon":     "#d1fae5", "mismatch":  "#fef3c7",
    "nib":       "#fee2e2", "alt":       "#f8f8f6",
    "border":    "#d3d1c7",
}

VERDICT_COLS = [
    "Order ID", "Customer Email", "Plan Name", "Plan Type",
    "Grand Total", "Status", "Transaction ID", "Tracking ID",
    "Created At", "Bank", "Bank_Amount", "Verdict", "Diff (USD)",
    "Bank_Status", "PSP_Name",
]


def _available_cols(df, cols):
    return [c for c in cols if c in df.columns]


def _make_workbook(buf):
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "strings_to_numbers": False,
                                    "nan_inf_to_errors": True})
    fmt = {
        "hdr":      wb.add_format({"bold": True, "bg_color": C["header_bg"],
                                    "font_color": C["header_fg"], "border": 1, "text_wrap": True}),
        "recon":    wb.add_format({"bg_color": C["recon"]}),
        "mismatch": wb.add_format({"bg_color": C["mismatch"]}),
        "nib":      wb.add_format({"bg_color": C["nib"]}),
        "alt":      wb.add_format({"bg_color": C["alt"]}),
        "money":    wb.add_format({"num_format": "#,##0.00"}),
        "pct":      wb.add_format({"num_format": "0.00%"}),
        "bold":     wb.add_format({"bold": True}),
    }
    return wb, fmt


def _write_df(ws, df, fmt, row_offset=0):
    cols = list(df.columns)
    for c, col in enumerate(cols):
        ws.write(row_offset, c, col, fmt["hdr"])
    verdict_col = cols.index("Verdict") if "Verdict" in cols else None
    for r, (_, row) in enumerate(df.iterrows(), start=row_offset + 1):
        row_fmt = None
        if verdict_col is not None:
            v = row.iloc[verdict_col]
            if v == "RECONCILED":        row_fmt = fmt["recon"]
            elif v == "AMOUNT MISMATCH": row_fmt = fmt["mismatch"]
            elif "NOT IN" in str(v):     row_fmt = fmt["nib"]
            else:                        row_fmt = fmt["alt"] if r % 2 == 0 else None
        for c, val in enumerate(row):
            if row_fmt:
                ws.write(r, c, val, row_fmt)
            else:
                ws.write(r, c, val)


def _auto_width(ws, df, max_w=40):
    for c, col in enumerate(df.columns):
        data_max = df[col].astype(str).str.len().max() if len(df) else 0
        ws.set_column(c, c, min(max(len(str(col)) + 2, data_max + 1, 8), max_w))


def write_outputs(results, api_df, start_date, end_date, fmt="XLSX"):
    """Generate all output files. Returns dict: filename → BytesIO"""
    out = {}

    if "combined" not in results:
        # Only produce Order Wise if no Phase 1 ran
        try:
            from engine.report_order_wise import write_order_wise_excel
            ow_buf = write_order_wise_excel(api_df, results, results.get("phase2", {}))
            if ow_buf:
                out[f"Order_Wise_{start_date}_{end_date}.xlsx"] = ow_buf
        except Exception as _e:
            print(f"Order Wise skipped: {_e}")
        return out

    combined = results["combined"]

    # ── 1. Summary workbook ───────────────────────────────────────────────
    buf = io.BytesIO()
    wb, wfmt = _make_workbook(buf)
    ws_sum = wb.add_worksheet("Summary")
    _write_summary_sheet(ws_sum, wfmt, results)
    for bank_key, bank_df in results.items():
        if bank_key in ("summary", "combined", "bp_raw", "pp_raw", "phase2") \
                or not isinstance(bank_df, pd.DataFrame) or bank_df.empty:
            continue
        ws = wb.add_worksheet(bank_key[:31])
        show_df = bank_df[_available_cols(bank_df, VERDICT_COLS)]
        _write_df(ws, show_df, wfmt)
        _auto_width(ws, show_df)
    ws_all = wb.add_worksheet("All Results")
    _write_df(ws_all, combined[_available_cols(combined, VERDICT_COLS)], wfmt)
    _auto_width(ws_all, combined[_available_cols(combined, VERDICT_COLS)])
    wb.close()
    buf.seek(0)
    out[f"Summary_{start_date}_{end_date}.xlsx"] = buf

    # ── 2. Phase 2 PSP results ────────────────────────────────────────────
    if results.get("phase2"):
        buf2 = io.BytesIO()
        wb2, wfmt2 = _make_workbook(buf2)
        for psp_key, psp_df in results["phase2"].items():
            if not isinstance(psp_df, pd.DataFrame) or psp_df.empty:
                continue
            ws2 = wb2.add_worksheet(psp_key[:31])
            show = list(dict.fromkeys(_available_cols(psp_df,
                ["PSP", "Orch_Amount", "PSP_Amount", "Diff (USD)", "Verdict"]
                + list(psp_df.columns))))
            _write_df(ws2, psp_df[show], wfmt2)
            _auto_width(ws2, psp_df[show])
        wb2.close()
        buf2.seek(0)
        out[f"Phase2_PSP_{start_date}_{end_date}.xlsx"] = buf2

    # ── 3. Mismatches ─────────────────────────────────────────────────────
    mis = combined[combined["Verdict"] == "AMOUNT MISMATCH"]
    if not mis.empty:
        out[f"Amount_Mismatches_{start_date}_{end_date}.xlsx"] = _df_to_xlsx(
            mis[_available_cols(mis, VERDICT_COLS)])

    # ── 4. Not in bank ────────────────────────────────────────────────────
    nib = combined[combined["Verdict"] == "NOT IN BANK"]
    if not nib.empty:
        out[f"Not_In_Bank_{start_date}_{end_date}.xlsx"] = _df_to_xlsx(
            nib[_available_cols(nib, VERDICT_COLS)])

    # ── 5. Per-bank files ─────────────────────────────────────────────────
    for bank_key, bank_df in results.items():
        if bank_key in ("summary", "combined", "bp_raw", "pp_raw", "phase2") \
                or not isinstance(bank_df, pd.DataFrame) or bank_df.empty:
            continue
        label = bank_key.replace("_", " ").title()
        out[f"{label}_Verdict_{start_date}_{end_date}.xlsx"] = _df_to_xlsx(
            bank_df[_available_cols(bank_df, VERDICT_COLS)])

    # ── 6. CSV versions ───────────────────────────────────────────────────
    if fmt in ("CSV", "Both"):
        out[f"All_Results_{start_date}_{end_date}.csv"] = _df_to_csv(
            combined[_available_cols(combined, VERDICT_COLS)])
    if fmt == "CSV":
        out = {k: v for k, v in out.items() if not k.endswith(".xlsx")}

    # ── 7. Order Wise Report ──────────────────────────────────────────────
    try:
        from engine.report_order_wise import write_order_wise_excel
        ow_buf = write_order_wise_excel(api_df, results, results.get("phase2", {}))
        if ow_buf:
            out[f"Order_Wise_{start_date}_{end_date}.xlsx"] = ow_buf
    except Exception as _ow_e:
        print(f"Order Wise report error: {_ow_e}")

    return out


def _write_summary_sheet(ws, wfmt, results):
    summ = results.get("summary", {})
    rows = [
        ("Metric", "Value"),
        ("Total API enabled orders", summ.get("total_api", "-")),
        ("Orders matched to banks",  summ.get("total_matched", "-")),
        ("Reconciled",               summ.get("reconciled", "-")),
        ("Reconciled %",             f"{summ.get('recon_pct', 0):.2f}%"),
        ("Amount mismatches",        summ.get("mismatches", "-")),
        ("Not in bank",              summ.get("not_in_bank", "-")),
    ]
    for r, (label, value) in enumerate(rows):
        f = wfmt["hdr"] if r == 0 else None
        ws.write(r, 0, label, f)
        ws.write(r, 1, value, f)
    ws.set_column(0, 0, 30)
    ws.set_column(1, 1, 20)
    if "combined" in results:
        combined = results["combined"]
        r = len(rows) + 1
        for i, col in enumerate(["Bank", "Reconciled", "Mismatch", "Not In Bank", "Total"]):
            ws.write(r, i, col, wfmt["hdr"])
        r += 1
        for bank in combined.get("Bank", pd.Series()).dropna().unique():
            sub = combined[combined["Bank"] == bank]
            ws.write(r, 0, bank)
            ws.write(r, 1, int((sub["Verdict"] == "RECONCILED").sum()))
            ws.write(r, 2, int((sub["Verdict"] == "AMOUNT MISMATCH").sum()))
            ws.write(r, 3, int((sub["Verdict"] == "NOT IN BANK").sum()))
            ws.write(r, 4, len(sub))
            r += 1


def _df_to_xlsx(df):
    buf = io.BytesIO()
    wb, wfmt = _make_workbook(buf)
    ws = wb.add_worksheet("Data")
    _write_df(ws, df, wfmt)
    _auto_width(ws, df)
    wb.close()
    buf.seek(0)
    return buf


def _df_to_csv(df):
    buf = io.BytesIO()
    buf.write(df.to_csv(index=False).encode("utf-8"))
    buf.seek(0)
    return buf
