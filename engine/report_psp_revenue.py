"""
engine/report_psp_revenue.py
PSP Revenue Report — Revenue confirmed by actual PSPs (NOT orchestrators)
Country data sourced from Orchestrator (Bridgerpay/Payprocc) files.
  Sheet 1: Total Revenue by PSP
  Sheet 2: Country-wise PSP Revenue  
  Sheet 3: Package-wise PSP Revenue
"""
import io
import pandas as pd
import numpy as np
import xlsxwriter
from engine.loader import find_col, to_numeric_col

BRAND = {"bg":"#0a1628","acc":"#f5a623","white":"#ffffff"}

def _pkg(pt):
    if not pt or pd.isna(pt): return "Other"
    s = str(pt)
    return (s.split(" | ")[0] if " | " in s else s).replace("Challenge","").strip()

def _plan(pt):
    if not pt or pd.isna(pt): return "N/A"
    s = str(pt)
    return s.split(" | ")[-1].strip().upper() if " | " in s else "N/A"


def _build_country_lookup(results):
    """Build country lookup from Orchestrator files (BP + PP)."""
    lookup = {}

    def _find_country_col(df):
        """Find any column containing 'country' or 'region' (case-insensitive)."""
        for col in df.columns:
            cl = col.lower()
            if "country" in cl or "region" in cl or "location" in cl:
                return col
        return None

    # Bridgerpay
    bp = results.get("bp_raw", pd.DataFrame())
    if not bp.empty:
        bp_ctry = _find_country_col(bp)
        bp_moi  = find_col(bp, ["merchantOrderId","merchant_order_id"])
        bp_tid  = find_col(bp, ["transactionId","transaction_id"])
        bp_id   = find_col(bp, ["id","Id","ID"])
        if bp_ctry:
            for _, row in bp.iterrows():
                ctry = str(row.get(bp_ctry, "")).strip()
                if not ctry or ctry == "nan": ctry = "Unknown"
                for col in [bp_moi, bp_tid, bp_id]:
                    if col and row.get(col):
                        lookup[str(row[col])] = ctry

    # Payprocc
    pp = results.get("pp_raw", pd.DataFrame())
    if not pp.empty:
        pp_ctry = _find_country_col(pp)
        pp_mid  = find_col(pp, ["Merchant Order ID","Payment Public ID"])
        if pp_ctry:
            for _, row in pp.iterrows():
                ctry = str(row.get(pp_ctry, "")).strip()
                if not ctry or ctry == "nan": ctry = "Unknown"
                if pp_mid and row.get(pp_mid):
                    lookup[str(row[pp_mid])] = ctry

    return lookup


def _collect_psp_rows(api_df, results, country_lookup):
    """
    Collect ALL PSP-confirmed rows.
    Revenue = API Grand Total (USD) for consistency with dashboard.
    """
    phase2 = results.get("phase2", {})
    rows = []

    # ── Build API lookup (vectorized) ────────────────────────────────────
    api_tid = find_col(api_df, ["Transaction ID","TransactionID"])
    api_trk = find_col(api_df, ["Tracking ID","TrackingID"])
    api_pt  = find_col(api_df, ["Plan Type","PlanType"])
    api_gt  = find_col(api_df, ["Grand Total","GrandTotal"])

    # Build fast lookup: key → {revenue, package, plan}
    api_lookup = {}
    if api_gt:
        gt_numeric = to_numeric_col(api_df[api_gt].astype(str))
        for col in [api_tid, api_trk]:
            if not col: continue
            for idx, k in api_df[col].items():
                k = str(k).strip()
                if k and k != "nan":
                    pt_val = str(api_df.at[idx, api_pt]) if api_pt else ""
                    api_lookup[k] = {
                        "revenue": float(gt_numeric.at[idx] or 0),
                        "package": _pkg(pt_val),
                        "plan":    _plan(pt_val),
                    }

    def _get_info(tid):
        return api_lookup.get(str(tid).strip(), {"revenue": 0, "package": "Unknown", "plan": "N/A"})

    # ── Phase 2 PSPs ─────────────────────────────────────────────────────
    p2_labels = {
        "nuvei_ni":"Nuvei NI","nuvei_aq":"Nuvei AQ","axcess":"Axcess",
        "trustpay":"Trust Payment","payabl":"Payabl","paysafe_bp":"Paysafe (BP)",
        "paysafe_pp":"Paysafe (PP)","paypal":"PayPal","unlimit":"Unlimit",
        "dlocal":"DLocal","skrill":"Skrill","confirmo_bp":"Confirmo (BP)",
        "zen_bp":"ZEN (BP)",
    }
    for key, label in p2_labels.items():
        df = phase2.get(key, pd.DataFrame())
        if df is None or df.empty or "Verdict" not in df.columns:
            continue
        rec = df[df["Verdict"].isin(["RECONCILED","AMOUNT MISMATCH"])].copy()
        if rec.empty: continue

        # _api_tid added by _match — contains the API Transaction ID
        tid_col = find_col(rec, ["_api_tid","merchantOrderId","Merchant Order ID",
                                  "Payment Public ID"])
        psp_ctry = find_col(rec, ["Country","country","billingCountry","cardCountry",
                                   "CustomerCountry","customer_country"])

        for _, r in rec.iterrows():
            tid  = str(r.get(tid_col, "")).strip() if tid_col else ""
            info = _get_info(tid)
            rev  = info["revenue"]

            ctry = ""
            if psp_ctry:
                ctry = str(r.get(psp_ctry, "")).strip()
            if not ctry or ctry == "nan":
                ctry = country_lookup.get(tid, "Unknown")

            rows.append({
                "psp": label, "api_tid": tid, "revenue": rev,
                "country": ctry,
                "package": info["package"],
                "plan":    info["plan"],
            })

    # ── Phase 1 independent PSPs ─────────────────────────────────────────
    indep = {"coinsbuy":"Coinsbuy","zen":"ZEN","confirmo":"Confirmo","tcpay":"TC Pay"}
    for key, label in indep.items():
        df = results.get(key, pd.DataFrame())
        if df.empty or "Verdict" not in df.columns: continue
        rec = df[df["Verdict"].isin(["RECONCILED","AMOUNT MISMATCH"])].copy()
        if rec.empty: continue

        tid_col = find_col(rec, ["Transaction ID","TransactionID"])
        trk_col = find_col(rec, ["Tracking ID","TrackingID"])
        id_col  = tid_col or trk_col

        for _, r in rec.iterrows():
            tid_val = str(r.get(id_col, "")).strip() if id_col else ""
            info    = _get_info(tid_val)
            ctry    = country_lookup.get(tid_val, "Unknown")
            rows.append({
                "psp": label, "api_tid": tid_val, "revenue": info["revenue"],
                "country": ctry,
                "package": info["package"],
                "plan":    info["plan"],
            })

    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["psp","api_tid","revenue","country","package","plan"])


def build_psp_revenue(api_df, results):
    """Build all 3 revenue breakdowns using ONLY PSP-confirmed data."""
    country_lookup = _build_country_lookup(results)
    psp_df = _collect_psp_rows(api_df, results, country_lookup)
    if psp_df.empty:
        return None

    # Use _build_psp_set for TOTAL to match dashboard exactly
    from engine.report_order_wise import _build_psp_set
    dashboard_psp_ids = _build_psp_set(results, results.get("phase2", {}))

    # Dashboard total: filter API to PSP-confirmed TIDs
    api_tid_col = find_col(api_df, ["Transaction ID","TransactionID"])
    api_gt_col  = find_col(api_df, ["Grand Total","GrandTotal"])
    if api_tid_col and api_gt_col:
        api_psp = api_df[api_df[api_tid_col].isin(dashboard_psp_ids)].copy()
        api_psp["_gt"] = to_numeric_col(api_psp[api_gt_col].astype(str))
        dedup_orders = len(api_psp)
        dedup_rev    = round(float(api_psp["_gt"].sum()), 2)
    else:
        psp_dedup = psp_df.drop_duplicates(subset="api_tid", keep="first")
        dedup_orders = len(psp_dedup)
        dedup_rev    = round(psp_dedup["revenue"].sum(), 2)

    # Also build dedup for country/package breakdowns using API data
    if api_tid_col:
        api_psp_set = set(api_psp[api_tid_col].dropna()) if api_tid_col else set()
        psp_dedup = psp_df[psp_df["api_tid"].isin(api_psp_set)].drop_duplicates(subset="api_tid", keep="first")
    else:
        psp_dedup = psp_df.drop_duplicates(subset="api_tid", keep="first")

    # ── Sheet 1: Revenue by PSP ───────────────────────────────────────────
    by_psp = (psp_df.groupby("psp")
              .agg(Orders=("revenue","count"), Revenue=("revenue","sum"))
              .reset_index().rename(columns={"psp":"PSP"})
              .sort_values("Revenue", ascending=False))
    by_psp["Revenue"] = by_psp["Revenue"].round(2)
    by_psp["Avg Order"] = (by_psp["Revenue"] / by_psp["Orders"].replace(0,1)).round(2)
    by_psp["% Share"]   = (by_psp["Revenue"] / max(by_psp["Revenue"].sum(),1) * 100).round(1)
    total = pd.DataFrame([{"PSP":"TOTAL",
        "Orders":dedup_orders, "Revenue":dedup_rev,
        "Avg Order":round(dedup_rev/max(dedup_orders,1),2), "% Share":100.0}])
    by_psp = pd.concat([by_psp, total], ignore_index=True)

    # ── Sheet 2: Revenue by Country (deduplicated) ────────────────────────
    by_country = (psp_dedup.groupby("country")
                  .agg(Orders=("revenue","count"), Revenue=("revenue","sum"))
                  .reset_index().rename(columns={"country":"Country"})
                  .sort_values("Revenue", ascending=False))
    by_country["Revenue"] = by_country["Revenue"].round(2)
    total_c = pd.DataFrame([{"Country":"TOTAL",
        "Orders":dedup_orders, "Revenue":dedup_rev}])
    by_country = pd.concat([by_country, total_c], ignore_index=True)

    # ── Sheet 3: Revenue by Package (deduplicated) ────────────────────────
    by_package = (psp_dedup.groupby(["package","plan"])
                  .agg(Orders=("revenue","count"), Revenue=("revenue","sum"))
                  .reset_index().rename(columns={"package":"Package","plan":"Plan"})
                  .sort_values(["Package","Revenue"], ascending=[True,False]))
    by_package["Revenue"] = by_package["Revenue"].round(2)
    subs = (by_package.groupby("Package")
            .agg(Orders=("Orders","sum"),Revenue=("Revenue","sum"))
            .reset_index())
    subs["Plan"] = "— SUBTOTAL —"
    subs["Revenue"] = subs["Revenue"].round(2)
    by_package = pd.concat([by_package, subs], ignore_index=True)
    by_package = by_package.sort_values(["Package","Plan"])
    grand = pd.DataFrame([{"Package":"GRAND TOTAL","Plan":"",
        "Orders":dedup_orders, "Revenue":dedup_rev}])
    by_package = pd.concat([by_package, grand], ignore_index=True)

    return {"by_psp": by_psp, "by_country": by_country, "by_package": by_package}


def write_psp_revenue_excel(api_df, results, start_date, end_date):
    """Write PSP Revenue Report to Excel."""
    data = build_psp_revenue(api_df, results)
    if not data: return None

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"nan_inf_to_errors": True})

    tf = wb.add_format({"bold":True,"bg_color":BRAND["bg"],"font_color":BRAND["acc"],
                         "font_size":13,"align":"center","valign":"vcenter"})
    hf = wb.add_format({"bold":True,"bg_color":"#1a3a6b","font_color":"#fff",
                         "align":"center","border":1,"text_wrap":True})
    nf = wb.add_format({"bold":True,"border":1,"bg_color":"#f5f8ff"})
    nu = wb.add_format({"border":1,"num_format":"#,##0","align":"right"})
    af = wb.add_format({"border":1,"num_format":"$#,##0.00","align":"right"})
    pf = wb.add_format({"border":1,"num_format":"0.0","align":"center"})
    tn = wb.add_format({"bold":True,"bg_color":BRAND["bg"],"font_color":"#fff","border":1})
    tnu= wb.add_format({"bold":True,"bg_color":BRAND["bg"],"font_color":"#fff",
                         "border":1,"num_format":"#,##0","align":"right"})
    ta = wb.add_format({"bold":True,"bg_color":BRAND["bg"],"font_color":"#fff",
                         "border":1,"num_format":"$#,##0.00","align":"right"})
    sf = wb.add_format({"bold":True,"bg_color":"#e8f0fb","border":1})
    snu= wb.add_format({"bold":True,"bg_color":"#e8f0fb","border":1,"num_format":"#,##0","align":"right"})
    sa = wb.add_format({"bold":True,"bg_color":"#e8f0fb","border":1,"num_format":"$#,##0.00","align":"right"})

    fmap = {"n":nf,"u":nu,"a":af,"p":pf}
    tmap = {"n":tn,"u":tnu,"a":ta,"p":ta}
    smap = {"n":sf,"u":snu,"a":sa,"p":sa}

    def ws(sheet_name, df, title, cols):
        s = wb.add_worksheet(sheet_name)
        s.freeze_panes(2,0)
        s.set_row(0,26)
        s.merge_range(0,0,0,len(cols)-1,title,tf)
        for ci,(h,w,t) in enumerate(cols):
            s.write(1,ci,h,hf); s.set_column(ci,ci,w)
        for ri,(_,row) in enumerate(df.iterrows(),start=2):
            is_t = str(row.iloc[0]).upper().startswith(("TOTAL","GRAND"))
            is_s = "SUBTOTAL" in str(row.get("Plan","")).upper()
            for ci,(h,w,t) in enumerate(cols):
                v = row.iloc[ci] if ci < len(row) else ""
                f = tmap[t] if is_t else smap[t] if is_s else fmap[t]
                s.write(ri,ci,v,f)

    p = f"{start_date} → {end_date}"
    ws("Revenue by PSP", data["by_psp"],
       f"FundedNext — Total PSP Revenue  |  {p}",
       [("PSP",22,"n"),("Orders",12,"u"),("Revenue (USD)",18,"a"),
        ("Avg Order",14,"a"),("% Share",10,"p")])
    ws("Revenue by Country", data["by_country"],
       f"FundedNext — Country-wise PSP Revenue  |  {p}",
       [("Country",24,"n"),("Orders",12,"u"),("Revenue (USD)",18,"a")])
    ws("Revenue by Package", data["by_package"],
       f"FundedNext — Package-wise PSP Revenue  |  {p}",
       [("Package",26,"n"),("Plan",12,"n"),("Orders",12,"u"),("Revenue (USD)",18,"a")])

    wb.close()
    buf.seek(0)
    return buf
