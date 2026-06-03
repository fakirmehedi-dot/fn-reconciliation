"""
engine/report_phase_summary.py
Computes Phase 1 and Phase 2 summary tables with exact columns:
Phase 1: Bank Name | API Qty | API Amt | Bank Qty | Bank Amt |
         Mismatch Qty | Mismatch Amt | Not in Bank Qty | Not in Bank Amt |
         Extra in Bank Qty | Extra in Bank Amt | Match %
Phase 2: PSP Name | Orch Qty | Orch Amt | PSP Qty | PSP Amt |
         Mismatch Qty | Mismatch Amt | Not in PSP Qty | Not in PSP Amt |
         Extra in PSP Qty | Extra in PSP Amt | Match %
"""
import pandas as pd
import numpy as np
import io
import xlsxwriter
from engine.loader import normalize, find_col, to_numeric_col


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 Summary
# ─────────────────────────────────────────────────────────────────────────────

def _extra_in_bank_bp(api_df, bp_raw):
    """Count BP rows not found in any API Transaction ID."""
    if bp_raw is None or bp_raw.empty: return 0, 0.0
    moi_col = find_col(bp_raw, ["merchantOrderId","merchant_order_id"])
    amt_col = find_col(bp_raw, ["amount","Amount"])
    if not moi_col: return 0, 0.0
    api_tids = set(api_df.get("Transaction ID", pd.Series()).dropna())
    extra = bp_raw[~bp_raw[moi_col].isin(api_tids)]
    amt = to_numeric_col(extra[amt_col].astype(str)).sum() if amt_col else 0.0
    return len(extra), float(amt)


def _extra_in_bank_pp(api_df, pp_raw):
    """Count PP rows not found in any API Transaction ID."""
    if pp_raw is None or pp_raw.empty: return 0, 0.0
    moi_col = find_col(pp_raw, ["Merchant Order ID","merchant_order_id","MerchantOrderID"])
    amt_col = find_col(pp_raw, ["_usd","Amount","amount"])
    if not moi_col: return 0, 0.0
    api_tids = set(api_df.get("Transaction ID", pd.Series()).dropna())
    extra = pp_raw[~pp_raw[moi_col].isin(api_tids)]
    amt = to_numeric_col(extra[amt_col].astype(str)).sum() if amt_col else 0.0
    return len(extra), float(amt)


def build_phase1_summary(api_df, results, p1_label="Period 1", p2_label="Period 2",
                          p1_api=None, p2_api=None):
    """
    Build Phase 1 summary rows.
    Returns list of dicts with all required columns.
    """
    bank_order = ["bridgerpay", "payprocc", "coinsbuy", "tcpay", "zen", "confirmo"]
    bank_labels = {
        "bridgerpay": "Bridgerpay",
        "payprocc":   "Payprocc",
        "coinsbuy":   "Coinsbuy",
        "tcpay":      "TC Pay",
        "zen":        "ZEN",
        "confirmo":   "Confirmo",
    }

    rows = []
    gt_col  = find_col(api_df, ["Grand Total","GrandTotal"])
    tid_col = find_col(api_df, ["Transaction ID","TransactionID"])
    trk_col = find_col(api_df, ["Tracking ID","TrackingID"])
    api_df  = api_df.copy()
    if gt_col:
        api_df["_gt"] = to_numeric_col(api_df[gt_col].astype(str))

    # Each bank uses either Transaction ID or Tracking ID for its prefix
    prefixes = {
        "bridgerpay": ("BP_",  tid_col),
        "payprocc":   ("PP_",  tid_col),
        "coinsbuy":   ("B2B_", trk_col),   # B2B_ is in Tracking ID
        "tcpay":      ("OP-",  tid_col),
        "zen":        ("ZP_",  tid_col),
        "confirmo":   ("CFM_", trk_col),   # CFM_ is in Tracking ID
    }

    for key in bank_order:
        label = bank_labels.get(key, key)
        df = results.get(key, pd.DataFrame())
        pfx, col = prefixes.get(key, ("", tid_col))
        api_sub = api_df[api_df[col].astype(str).str.startswith(pfx, na=False)] if col and pfx else pd.DataFrame()

        api_qty = len(api_sub)
        api_amt = float(api_sub["_gt"].sum()) if "_gt" in api_sub.columns and not api_sub.empty else 0.0

        if df.empty:
            rows.append(_empty_row(label, api_qty, api_amt))
            continue

        # Bank qty/amt — rows where bank found a match
        matched  = df[df["Verdict"].isin(["RECONCILED","AMOUNT MISMATCH"])]
        bank_qty = len(matched)
        ba_col   = find_col(df, ["Bank_Amount","bank_amount"])
        bank_amt = float(to_numeric_col(matched[ba_col].astype(str)).sum()) if ba_col else 0.0

        recon    = df[df["Verdict"] == "RECONCILED"]
        mismatch = df[df["Verdict"] == "AMOUNT MISMATCH"]
        not_in   = df[df["Verdict"] == "NOT IN BANK"]

        mm_qty = len(mismatch)
        mm_amt = float(to_numeric_col(mismatch[ba_col].astype(str) if ba_col else pd.Series()).sum()) if ba_col else 0.0
        # Mismatch amt = diff between API amt and bank amt for mismatched rows
        if ba_col and gt_col:
            gt2 = find_col(df, ["Grand Total","_gt","GrandTotal"])
            if gt2:
                mm_api = float(to_numeric_col(mismatch[gt2].astype(str)).sum())
                mm_bk  = float(to_numeric_col(mismatch[ba_col].astype(str)).sum())
                mm_amt = mm_api - mm_bk
            else:
                mm_amt = 0.0

        nib_qty = len(not_in)
        nib_col = find_col(df, ["Grand Total","_gt","GrandTotal"])
        nib_amt = float(to_numeric_col(not_in[nib_col].astype(str)).sum()) if nib_col else 0.0

        # Extra in bank — bank rows not in API
        extra_qty, extra_amt = 0, 0.0
        bp_raw = results.get("bp_raw")
        pp_raw = results.get("pp_raw")
        if key == "bridgerpay" and bp_raw is not None:
            extra_qty, extra_amt = _extra_in_bank_bp(api_df, bp_raw)
        elif key == "payprocc" and pp_raw is not None:
            extra_qty, extra_amt = _extra_in_bank_pp(api_df, pp_raw)

        match_pct = len(recon) / api_qty * 100 if api_qty else 0.0

        rows.append({
            "Bank Name":           label,
            "API Qty":             api_qty,
            "API Amt":             round(api_amt, 2),
            "Bank Qty":            bank_qty,
            "Bank Amt":            round(bank_amt, 2),
            "Mismatch Qty":        api_qty - bank_qty,
            "Mismatch Amt":        round(api_amt - bank_amt, 2),
            "Not in Bank Qty":     nib_qty,
            "Not in Bank Amt":     round(nib_amt, 2),
            "Extra in Bank Qty":   extra_qty,
            "Extra in Bank Amt":   round(extra_amt, 2),
            "Match %":             round(match_pct, 2),
        })

    return rows


def _empty_row(label, api_qty=0, api_amt=0.0):
    return {
        "Bank Name": label, "API Qty": api_qty, "API Amt": round(api_amt,2),
        "Bank Qty": 0, "Bank Amt": 0.0,
        "Mismatch Qty": api_qty, "Mismatch Amt": round(api_amt,2),
        "Not in Bank Qty": api_qty, "Not in Bank Amt": round(api_amt,2),
        "Extra in Bank Qty": 0, "Extra in Bank Amt": 0.0, "Match %": 0.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 Summary
# ─────────────────────────────────────────────────────────────────────────────

def build_phase2_summary(results):
    """
    Build Phase 2 summary rows from phase2 results dict.
    Each Phase 2 DataFrame is PSP-as-base (or orch-as-base for TP/Paysafe).
    """
    psp_order = [
        "payabl","nuvei_aq","paysafe_bp","axcess","nuvei_ni",
        "paypal","trustpay","dlocal","paysafe_pp","unlimit","skrill",
        "confirmo_bp",
    ]
    psp_labels = {
        "payabl":     "Payabl",
        "nuvei_aq":   "Nuvei AQ",
        "paysafe_bp": "Paysafe (BP)",
        "axcess":     "Axcess/Truevo",
        "nuvei_ni":   "Nuvei NI",
        "paypal":     "PayPal",
        "trustpay":   "Trust Payment",
        "dlocal":     "DLocal",
        "paysafe_pp": "Paysafe (PP)",
        "unlimit":    "Unlimit",
        "skrill":     "Skrill",
        "confirmo_bp":"Confirmo (BP)",
    }

    p2 = results.get("phase2", {})
    rows = []

    for key in psp_order:
        label = psp_labels.get(key, key)
        df = p2.get(key, pd.DataFrame())

        if df is None or df.empty or "Verdict" not in df.columns:
            rows.append(_empty_p2_row(label))
            continue

        recon    = df[df["Verdict"] == "RECONCILED"]
        mismatch = df[df["Verdict"] == "AMOUNT MISMATCH"]
        not_in   = df[df["Verdict"].isin(["NOT IN ORCH","NOT IN PSP"])]

        orch_qty = len(df)
        orch_amt = float(to_numeric_col(df.get("Orch_Amount", pd.Series()).astype(str)).sum())
        psp_qty  = len(recon) + len(mismatch)
        psp_amt  = float(to_numeric_col(df.get("PSP_Amount", pd.Series()).astype(str)).sum())
        mm_qty   = len(mismatch)
        mm_amt   = float((df.get("PSP_Amount", pd.Series()) - df.get("Orch_Amount", pd.Series())).fillna(0).sum())
        nip_qty  = len(not_in)
        nip_amt  = float(to_numeric_col(not_in.get("PSP_Amount", pd.Series()).astype(str)).sum())
        extra_qty= 0   # Not directly available in current structure
        extra_amt= 0.0
        match_pct= len(recon) / orch_qty * 100 if orch_qty else 0.0

        rows.append({
            "PSP Name":          label,
            "Orchestrator Qty":  orch_qty,
            "Orchestrator Amt":  round(orch_amt, 2),
            "PSP Qty":           psp_qty,
            "PSP Amt":           round(psp_amt, 2),
            "Mismatch Qty":      orch_qty - psp_qty,
            "Mismatch Amt":      round(orch_amt - psp_amt, 2),
            "Not in PSP Qty":    nip_qty,
            "Not in PSP Amt":    round(nip_amt, 2),
            "Extra in PSP Qty":  extra_qty,
            "Extra in PSP Amt":  extra_amt,
            "Match %":           round(match_pct, 2),
        })

    return rows


def _empty_p2_row(label):
    return {k: (label if k=="PSP Name" else 0) for k in
            ["PSP Name","Orchestrator Qty","Orchestrator Amt","PSP Qty","PSP Amt",
             "Mismatch Qty","Mismatch Amt","Not in PSP Qty","Not in PSP Amt",
             "Extra in PSP Qty","Extra in PSP Amt","Match %"]}


# ─────────────────────────────────────────────────────────────────────────────
# Excel Writer
# ─────────────────────────────────────────────────────────────────────────────

BRAND = {"bg": "#0a1628", "accent": "#f5a623", "green": "#00c853",
         "red": "#e53935", "white": "#ffffff", "light": "#f8faff"}

def _wb_fmts(wb):
    def fmt(**kw): return wb.add_format(kw)
    return {
        "title":  fmt(bold=True, bg_color=BRAND["bg"], font_color=BRAND["accent"],
                      font_size=14, align="center", valign="vcenter", border=0),
        "sub":    fmt(bold=True, bg_color=BRAND["bg"], font_color=BRAND["white"],
                      align="center", valign="vcenter", border=0, font_size=10),
        "hdr":    fmt(bold=True, bg_color="#1a3a6b", font_color=BRAND["white"],
                      align="center", valign="vcenter", border=1, text_wrap=True),
        "name":   fmt(bg_color=BRAND["light"], bold=True, border=1),
        "num":    fmt(border=1, num_format="#,##0", align="right"),
        "amt":    fmt(border=1, num_format="#,##0.00", align="right"),
        "mm_num": fmt(border=1, num_format="#,##0", align="right", font_color=BRAND["red"]),
        "mm_amt": fmt(border=1, num_format="#,##0.00", align="right", font_color=BRAND["red"]),
        "pct_ok": fmt(border=1, num_format='0.00"%"', align="center",
                      bg_color="#e8f5e9", bold=True),
        "pct_lo": fmt(border=1, num_format='0.00"%"', align="center",
                      bg_color="#ffebee", bold=True, font_color=BRAND["red"]),
        "total":  fmt(bold=True, bg_color=BRAND["bg"], font_color=BRAND["white"],
                      border=1, num_format="#,##0"),
        "total_a":fmt(bold=True, bg_color=BRAND["bg"], font_color=BRAND["white"],
                      border=1, num_format="#,##0.00"),
        "total_n":fmt(bold=True, bg_color=BRAND["bg"], font_color=BRAND["white"],
                      border=1),
    }


def write_summary_excel(p1_rows, p2_rows, start_date, end_date,
                         p1_label="", p2_label=""):
    """Write both summary tables to Excel. Returns BytesIO."""
    buf = io.BytesIO()
    wb  = xlsxwriter.Workbook(buf, {"nan_inf_to_errors": True})
    f   = _wb_fmts(wb)

    # ── Phase 1 sheet ──────────────────────────────────────────────────────
    ws1 = wb.add_worksheet("Phase 1 – API vs Bank")
    ws1.set_column("A:A", 18)
    ws1.set_column("B:M", 16)
    ws1.freeze_panes(3, 1)

    ws1.merge_range("A1:M1",
        f"FundedNext  ·  Revenue Reconciliation  ·  Phase 1: API vs Bank  |  {start_date} → {end_date}",
        f["title"])
    ws1.set_row(0, 28)

    p1_cols = ["Bank Name","API Qty","API Amt","Bank Qty","Bank Amt",
               "Mismatch Qty (API-Bank)","Mismatch Amt (API-Bank)",
               "Not in Bank Qty","Not in Bank Amt",
               "Extra in Bank Qty","Extra in Bank Amt","Match %"]
    p1_data = ["Bank Name","API Qty","API Amt","Bank Qty","Bank Amt",
               "Mismatch Qty","Mismatch Amt",
               "Not in Bank Qty","Not in Bank Amt",
               "Extra in Bank Qty","Extra in Bank Amt","Match %"]

    for ci, h in enumerate(p1_cols):
        ws1.write(1, ci, h, f["hdr"])

    totals = {k: 0 for k in p1_data[1:]}
    for ri, row in enumerate(p1_rows, start=2):
        pct = row["Match %"]
        pct_fmt = f["pct_ok"] if pct >= 95 else f["pct_lo"]
        ws1.write(ri, 0, row["Bank Name"], f["name"])
        cols_fmt = [
            (row["API Qty"],           f["num"]),
            (row["API Amt"],           f["amt"]),
            (row["Bank Qty"],          f["num"]),
            (row["Bank Amt"],          f["amt"]),
            (row["Mismatch Qty"],      f["mm_num"]),
            (row["Mismatch Amt"],      f["mm_amt"]),
            (row["Not in Bank Qty"],   f["mm_num"]),
            (row["Not in Bank Amt"],   f["mm_amt"]),
            (row["Extra in Bank Qty"], f["num"]),
            (row["Extra in Bank Amt"], f["amt"]),
            (row["Match %"],           pct_fmt),
        ]
        for ci, (val, fmt_) in enumerate(cols_fmt, start=1):
            ws1.write(ri, ci, val, fmt_)
        for k in p1_data[1:-1]:
            totals[k] = totals.get(k, 0) + row[k]

    tr = len(p1_rows) + 2
    ws1.write(tr, 0, "TOTAL", f["total_n"])
    tot_fmts = [f["total"],f["total_a"],f["total"],f["total_a"],
                f["total"],f["total_a"],f["total"],f["total_a"],
                f["total"],f["total_a"],f["total_n"]]
    tot_vals = [totals["API Qty"],totals["API Amt"],totals["Bank Qty"],totals["Bank Amt"],
                totals["Mismatch Qty"],totals["Mismatch Amt"],
                totals["Not in Bank Qty"],totals["Not in Bank Amt"],
                totals["Extra in Bank Qty"],totals["Extra in Bank Amt"],
                round(totals["API Qty"] and totals.get("Bank Qty",0)/totals["API Qty"]*100,2)]
    for ci, (v, fmt_) in enumerate(zip(tot_vals, tot_fmts), start=1):
        ws1.write(tr, ci, v, fmt_)

    # ── Phase 2 sheet ──────────────────────────────────────────────────────
    ws2 = wb.add_worksheet("Phase 2 – Orch vs PSP")
    ws2.set_column("A:A", 18)
    ws2.set_column("B:M", 16)
    ws2.freeze_panes(3, 1)

    ws2.merge_range("A1:M1",
        f"FundedNext  ·  Revenue Reconciliation  ·  Phase 2: Orchestrator vs PSP  |  {start_date} → {end_date}",
        f["title"])
    ws2.set_row(0, 28)

    p2_cols = ["PSP Name","Orchestrator Qty","Orchestrator Amt","PSP Qty","PSP Amt",
               "Mismatch Qty (O-PSP)","Mismatch Amt (O-PSP)",
               "Not in PSP Qty","Not in PSP Amt",
               "Extra in PSP Qty","Extra in PSP Amt","Match %"]
    p2_data = ["PSP Name","Orchestrator Qty","Orchestrator Amt","PSP Qty","PSP Amt",
               "Mismatch Qty","Mismatch Amt","Not in PSP Qty","Not in PSP Amt",
               "Extra in PSP Qty","Extra in PSP Amt","Match %"]

    for ci, h in enumerate(p2_cols):
        ws2.write(1, ci, h, f["hdr"])

    totals2 = {k: 0 for k in p2_data[1:]}
    for ri, row in enumerate(p2_rows, start=2):
        pct = row["Match %"]
        pct_fmt = f["pct_ok"] if pct >= 95 else f["pct_lo"]
        ws2.write(ri, 0, row["PSP Name"], f["name"])
        cols_fmt2 = [
            (row["Orchestrator Qty"],  f["num"]),
            (row["Orchestrator Amt"],  f["amt"]),
            (row["PSP Qty"],           f["num"]),
            (row["PSP Amt"],           f["amt"]),
            (row["Mismatch Qty"],      f["mm_num"]),
            (row["Mismatch Amt"],      f["mm_amt"]),
            (row["Not in PSP Qty"],    f["mm_num"]),
            (row["Not in PSP Amt"],    f["mm_amt"]),
            (row["Extra in PSP Qty"],  f["num"]),
            (row["Extra in PSP Amt"],  f["amt"]),
            (row["Match %"],           pct_fmt),
        ]
        for ci, (val, fmt_) in enumerate(cols_fmt2, start=1):
            ws2.write(ri, ci, val, fmt_)
        for k in p2_data[1:-1]:
            totals2[k] = totals2.get(k, 0) + row[k]

    tr2 = len(p2_rows) + 2
    ws2.write(tr2, 0, "TOTAL", f["total_n"])
    tot2_vals = [totals2["Orchestrator Qty"],totals2["Orchestrator Amt"],
                 totals2["PSP Qty"],totals2["PSP Amt"],
                 totals2["Mismatch Qty"],totals2["Mismatch Amt"],
                 totals2["Not in PSP Qty"],totals2["Not in PSP Amt"],
                 totals2["Extra in PSP Qty"],totals2["Extra in PSP Amt"], ""]
    tot2_fmts= [f["total"],f["total_a"],f["total"],f["total_a"],
                f["total"],f["total_a"],f["total"],f["total_a"],
                f["total"],f["total_a"],f["total_n"]]
    for ci, (v, fmt_) in enumerate(zip(tot2_vals, tot2_fmts), start=1):
        ws2.write(tr2, ci, v, fmt_)

    wb.close()
    buf.seek(0)
    return buf


# ─────────────────────────────────────────────────────────────────────────────
# HTML Dashboard
# ─────────────────────────────────────────────────────────────────────────────

def write_html_dashboard(p1_rows, p2_rows, start_date, end_date):
    """Generate HTML dashboard. Returns BytesIO."""
    def fmt_num(v):
        try: return f"{int(v):,}"
        except: return str(v)
    def fmt_amt(v):
        try: return f"${float(v):,.2f}"
        except: return str(v)
    def fmt_pct(v):
        try:
            p = float(v)
            cls = "ok" if p >= 95 else "lo"
            return f'<span class="pct {cls}">{p:.2f}%</span>'
        except: return str(v)
    def diff_cell(v):
        try:
            n = float(v)
            cls = "" if abs(n) < 0.01 else "red"
            return f'<span class="{cls}">{fmt_num(v) if isinstance(v,int) or str(v).lstrip("-").isdigit() else fmt_amt(v)}</span>'
        except: return str(v)

    def p1_row_html(r):
        return f"""<tr>
  <td class="name">{r['Bank Name']}</td>
  <td>{fmt_num(r['API Qty'])}</td><td>{fmt_amt(r['API Amt'])}</td>
  <td>{fmt_num(r['Bank Qty'])}</td><td>{fmt_amt(r['Bank Amt'])}</td>
  <td class="diff">{fmt_num(r['Mismatch Qty'])}</td><td class="diff">{fmt_amt(r['Mismatch Amt'])}</td>
  <td class="diff">{fmt_num(r['Not in Bank Qty'])}</td><td class="diff">{fmt_amt(r['Not in Bank Amt'])}</td>
  <td>{fmt_num(r['Extra in Bank Qty'])}</td><td>{fmt_amt(r['Extra in Bank Amt'])}</td>
  <td>{fmt_pct(r['Match %'])}</td>
</tr>"""

    def p2_row_html(r):
        return f"""<tr>
  <td class="name">{r['PSP Name']}</td>
  <td>{fmt_num(r['Orchestrator Qty'])}</td><td>{fmt_amt(r['Orchestrator Amt'])}</td>
  <td>{fmt_num(r['PSP Qty'])}</td><td>{fmt_amt(r['PSP Amt'])}</td>
  <td class="diff">{fmt_num(r['Mismatch Qty'])}</td><td class="diff">{fmt_amt(r['Mismatch Amt'])}</td>
  <td class="diff">{fmt_num(r['Not in PSP Qty'])}</td><td class="diff">{fmt_amt(r['Not in PSP Amt'])}</td>
  <td>{fmt_num(r['Extra in PSP Qty'])}</td><td>{fmt_amt(r['Extra in PSP Amt'])}</td>
  <td>{fmt_pct(r['Match %'])}</td>
</tr>"""

    p1_html = "\n".join(p1_row_html(r) for r in p1_rows)
    p2_html = "\n".join(p2_row_html(r) for r in p2_rows)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FundedNext — Revenue Reconciliation</title>
<style>
  :root{{--bg:#0a1628;--accent:#f5a623;--green:#00c853;--red:#e53935;
         --white:#fff;--light:#f0f4ff;--border:#d0d9ec}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',system-ui,sans-serif;background:#f4f7fc;color:#1a2540}}

  /* Header */
  .header{{background:var(--bg);padding:20px 32px;display:flex;
           align-items:center;justify-content:space-between}}
  .logo{{display:flex;align-items:center;gap:12px}}
  .logo-mark{{width:40px;height:40px;background:var(--accent);border-radius:8px;
              display:flex;align-items:center;justify-content:center;
              font-weight:900;font-size:18px;color:var(--bg)}}
  .logo-text{{color:var(--white);font-size:22px;font-weight:700;letter-spacing:.5px}}
  .logo-sub{{color:rgba(255,255,255,.55);font-size:12px;margin-top:2px}}
  .header-meta{{text-align:right;color:rgba(255,255,255,.7);font-size:13px}}
  .header-meta strong{{color:var(--accent);font-size:15px}}

  /* Cards */
  .page{{padding:24px 32px}}
  .section-title{{font-size:17px;font-weight:700;color:var(--bg);
                  margin:24px 0 12px;padding-left:10px;
                  border-left:4px solid var(--accent)}}

  /* Table */
  .table-wrap{{overflow-x:auto;border-radius:10px;
               box-shadow:0 2px 12px rgba(0,0,0,.08)}}
  table{{width:100%;border-collapse:collapse;background:var(--white);font-size:13px}}
  thead tr:first-child th{{background:var(--bg);color:var(--white);
                            padding:10px 12px;text-align:center;font-size:12px;
                            font-weight:600;white-space:nowrap}}
  thead tr:last-child th{{background:#1a3a6b;color:var(--white);
                           padding:8px 12px;text-align:center;font-size:11px;
                           font-weight:600;border:1px solid #2a4a8b}}
  tbody tr{{border-bottom:1px solid var(--border)}}
  tbody tr:hover{{background:#f5f8ff}}
  tbody tr:last-child{{background:var(--bg);color:var(--white);font-weight:700}}
  tbody tr:last-child td{{color:var(--white)!important}}
  td{{padding:9px 12px;text-align:right;white-space:nowrap}}
  td.name{{text-align:left;font-weight:600;color:#1a2540;padding-left:14px}}
  td.diff{{color:var(--red)}}
  .pct{{padding:3px 10px;border-radius:20px;font-weight:700;font-size:12px}}
  .pct.ok{{background:#e8f5e9;color:#1b5e20}}
  .pct.lo{{background:#ffebee;color:var(--red)}}
  .red{{color:var(--red)}}

  /* Footer */
  .footer{{text-align:center;padding:20px;color:#8090a8;font-size:12px;
           border-top:1px solid var(--border);margin-top:32px}}
</style>
</head>
<body>

<header class="header">
  <div class="logo">
    <div class="logo-mark">FN</div>
    <div>
      <div class="logo-text">FundedNext</div>
      <div class="logo-sub">Revenue Reconciliation Portal</div>
    </div>
  </div>
  <div class="header-meta">
    <div>Reconciliation Period</div>
    <strong>{start_date} → {end_date}</strong>
  </div>
</header>

<div class="page">

  <div class="section-title">Phase 1 — API vs Bank</div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th rowspan="2">Bank Name</th>
          <th colspan="2">API</th>
          <th colspan="2">Bank</th>
          <th colspan="2">Mismatch (API − Bank)</th>
          <th colspan="2">Not in Bank</th>
          <th colspan="2">Extra in Bank</th>
          <th rowspan="2">Match %</th>
        </tr>
        <tr>
          <th>Qty</th><th>Amount</th>
          <th>Qty</th><th>Amount</th>
          <th>Qty</th><th>Amount</th>
          <th>Qty</th><th>Amount</th>
          <th>Qty</th><th>Amount</th>
        </tr>
      </thead>
      <tbody>
{p1_html}
      </tbody>
    </table>
  </div>

  <div class="section-title" style="margin-top:32px">Phase 2 — Orchestrator vs PSP</div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th rowspan="2">PSP Name</th>
          <th colspan="2">Orchestrator</th>
          <th colspan="2">PSP</th>
          <th colspan="2">Mismatch (O − PSP)</th>
          <th colspan="2">Not in PSP</th>
          <th colspan="2">Extra in PSP</th>
          <th rowspan="2">Match %</th>
        </tr>
        <tr>
          <th>Qty</th><th>Amount</th>
          <th>Qty</th><th>Amount</th>
          <th>Qty</th><th>Amount</th>
          <th>Qty</th><th>Amount</th>
          <th>Qty</th><th>Amount</th>
        </tr>
      </thead>
      <tbody>
{p2_html}
      </tbody>
    </table>
  </div>

</div>

<footer class="footer">
  Generated by FundedNext Revenue Reconciliation Portal &nbsp;·&nbsp; {start_date} → {end_date}
</footer>
</body>
</html>"""

    buf = io.BytesIO()
    buf.write(html.encode("utf-8"))
    buf.seek(0)
    return buf
