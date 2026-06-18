"""FundedNext — Revenue Reconciliation Portal"""
import io, zipfile, datetime, traceback
import pandas as pd
import streamlit as st

st.set_page_config(page_title="FundedNext · Reconciliation",
                   page_icon="💰", layout="wide",
                   initial_sidebar_state="expanded")

st.markdown("""<style>
.fn-hdr{background:#0a1628;padding:13px 22px;border-radius:9px;
        display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
.fn-mark{width:36px;height:36px;background:#f5a623;border-radius:7px;
         font-weight:900;font-size:16px;color:#0a1628;
         display:flex;align-items:center;justify-content:center}
.fn-brand{color:#fff;font-size:18px;font-weight:700}
.fn-tag{color:rgba(255,255,255,.5);font-size:11px}
.fn-period{color:rgba(255,255,255,.7);font-size:12px;text-align:right}
.fn-period b{color:#f5a623}
.sec{font-size:13px;font-weight:700;color:#1a2540;margin:14px 0 7px;
     padding-left:7px;border-left:3px solid #f5a623}
.rt{width:100%;border-collapse:collapse;font-size:12px}
.rt th{background:#0a1628;color:#fff;padding:6px 9px;text-align:center;
       border:1px solid #1e3a6b;white-space:nowrap;font-size:11px}
.rt td{padding:6px 9px;text-align:right;border:1px solid #e0e6f0;white-space:nowrap}
.rt td.nm{text-align:left;font-weight:600;background:#f5f8ff;padding-left:11px}
.rt .d{color:#e53935;font-weight:600}
.rt .ok{background:#e8f5e9;color:#1b5e20;font-weight:700;border-radius:3px;padding:2px 7px;display:inline-block}
.rt .lo{background:#ffebee;color:#e53935;font-weight:700;border-radius:3px;padding:2px 7px;display:inline-block}
.rt .pa{background:#fff3e0;color:#e65100;font-weight:700;border-radius:3px;padding:2px 7px;display:inline-block}
.kpi-card{background:#fff;border:1px solid #dde3f0;border-radius:9px;
          padding:12px 15px;text-align:center;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.kpi-val{font-size:22px;font-weight:800;color:#0a1628}
.kpi-lbl{font-size:11px;color:#7a8aaa;margin-top:2px}
.kpi-sub{font-size:11px;font-weight:600;margin-top:1px}
.det-tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;
         font-weight:600;margin:2px 3px}
.det-ok{background:#e8f5e9;color:#1b5e20}
.det-warn{background:#fff3e0;color:#9a3412}
</style>""", unsafe_allow_html=True)


# Theme toggle





# ── Auto-detect functions ─────────────────────────────────────────────────────
def detect_phase1(filename):
    """Detect Phase 1 bank from filename."""
    n = filename.lower()
    for pattern, key in [
        ("bridgerpay","bridgerpay"),("bp_transaction","bridgerpay"),("bp_","bridgerpay"),
        ("payprocc","payprocc"),("pp_","payprocc"),
        ("coinsbuy","coinsbuy"),("coins_buy","coinsbuy"),
        ("zen_au","zen"),("zen au","zen"),("zen","zen"),
        ("confirmo","confirmo"),
        ("tc_pay","tcpay"),("tc pay","tcpay"),("tcpay","tcpay"),
    ]:
        if pattern in n:
            return key
    return None

def detect_phase2(filename):
    """Detect Phase 2 PSP from filename."""
    n = filename.lower()
    for pattern, key in [
        ("nuvei_ni","nuvei_ni"),("nuvei ni","nuvei_ni"),("nuvei-ni","nuvei_ni"),
        ("nuvei_aq","nuvei_aq"),("nuvei aq","nuvei_aq"),("nuvei-aq","nuvei_aq"),
        ("nuvei","nuvei_ni"),  # default nuvei → NI
        ("axcess","axcess"),("truevo","axcess"),
        ("trust payment","trustpay"),("trust_payment","trustpay"),("trustpay","trustpay"),
        ("payabl","payabl"),
        ("paysafe","paysafe"),
        ("paypal","paypal"),
        ("unlimit","unlimit"),
        ("dlocal","dlocal"),("d-local","dlocal"),
        ("skrill","skrill"),
        ("confirmo","confirmo_bp"),
    ]:
        if pattern in n:
            return key
    return None

BANK_LABELS = {"bridgerpay":"Bridgerpay","payprocc":"Payprocc","coinsbuy":"Coinsbuy",
               "zen":"ZEN","confirmo":"Confirmo","tcpay":"TC Pay"}
PSP_LABELS  = {"nuvei_ni":"Nuvei NI","nuvei_aq":"Nuvei AQ","axcess":"Axcess",
               "trustpay":"Trust Payment","payabl":"Payabl","paysafe":"Paysafe",
               "paypal":"PayPal","unlimit":"Unlimit","dlocal":"DLocal",
               "skrill":"Skrill","confirmo_bp":"Confirmo (BP)","zen_bp":"ZEN (BP)",
               "paysafe_bp":"Paysafe (BP)","paysafe_pp":"Paysafe (PP)"}

# ── Session state ─────────────────────────────────────────────────────────────
for k,v in [("results",None),("out_files",None),("run_done",False),
            ("api_df",None),("dup_df",None),("dup_detail",None),
            ("dl_order_wise",None),("dl_discrepancy",None),
            ("dl_comparison",None),("dl_psp_revenue",None),("dl_recon_gap",None),
            ("dl_free_report",None),("free_df",None)]:
    if k not in st.session_state: st.session_state[k] = v

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:

    mode = st.radio("Mode", ["Full Reconciliation","Comparison"], horizontal=True,
                    label_visibility="collapsed")
    st.markdown("---")
    if mode == "Full Reconciliation":
        st.markdown("**📅 Date Range**")
        c1,c2 = st.columns(2)
        with c1: start_date = st.date_input("From",value=datetime.date(2026,3,1),key="fs",label_visibility="collapsed")
        with c2: end_date   = st.date_input("To",value=datetime.date(2026,4,21),key="fe",label_visibility="collapsed")
        st.caption(f"{start_date} → {end_date}")
        p1_start=start_date; p1_end=end_date; p2_start=start_date; p2_end=end_date
    else:
        st.markdown("**📅 Period 1**")
        c1,c2=st.columns(2)
        with c1: p1_start=st.date_input("P1 From",value=datetime.date(2026,3,1),key="p1s",label_visibility="collapsed")
        with c2: p1_end  =st.date_input("P1 To",value=datetime.date(2026,3,21),key="p1e",label_visibility="collapsed")
        st.markdown("**📅 Period 2**")
        c3,c4=st.columns(2)
        with c3: p2_start=st.date_input("P2 From",value=datetime.date(2026,4,1),key="p2s",label_visibility="collapsed")
        with c4: p2_end  =st.date_input("P2 To",value=datetime.date(2026,4,21),key="p2e",label_visibility="collapsed")
        start_date=p1_start; end_date=p2_end

    st.markdown("---")
    st.markdown("**⚖️ Tolerances**")
    tol_usd  = st.number_input("USD ($)",value=0.01,step=0.01,format="%.2f")
    tol_usdt = st.number_input("USDT/Crypto ($)",value=2.00,step=0.10,format="%.2f")
    detect_dupes = st.checkbox("🔍 Detect duplicate TxIDs",value=True)
    min_amount   = st.number_input("Min amount ($)",value=0.0,step=1.0,format="%.2f")
    st.markdown("---")
    st.markdown("**📄 Output**")
    out_fmt = st.radio("Format",["XLSX","Both"],index=0,horizontal=True,label_visibility="collapsed")

    if st.session_state.run_done:
        st.markdown("---")
        st.markdown("### 📥 Downloads")
        for key,label,fname in [
            ("dl_order_wise","⬇️ Order Wise",f"FN_OrderWise_{start_date}_{end_date}.xlsx"),
            ("dl_discrepancy","⬇️ Discrepancy",f"FN_Discrepancy_{start_date}_{end_date}.xlsx"),
            ("dl_comparison","⬇️ Comparison",f"FN_Comparison_{p1_start}_{p2_end}.xlsx"),
            ("dl_psp_revenue","⬇️ PSP Revenue",f"FN_PSP_Revenue_{start_date}_{end_date}.xlsx"),
            ("dl_recon_gap","⬇️ Recon Gap Report",f"FN_Recon_Gap_{start_date}_{end_date}.xlsx"),
            ("dl_free_report","⬇️ Free Accounts",f"FN_Free_Accounts_{start_date}_{end_date}.xlsx"),
        ]:
            data = st.session_state.get(key)
            if data:
                st.download_button(label,data=data,file_name=fname,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,key=f"sb_{key}")

# ── Header ────────────────────────────────────────────────────────────────────
period_str = (f"{start_date} → {end_date}" if mode=="Full Reconciliation"
              else f"{p1_start}→{p1_end} | {p2_start}→{p2_end}")
st.markdown(f"""<div class="fn-hdr">
  <div style="display:flex;align-items:center;gap:10px">
    <div class="fn-mark">FN</div>
    <div><div class="fn-brand">FundedNext</div>
         <div class="fn-tag">Revenue Reconciliation Portal</div></div>
  </div>
  <div class="fn-period">{'Full Reconciliation' if mode=='Full Reconciliation' else 'Comparison'}<br>
    <b>{period_str}</b></div>
</div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# UPLOAD — 3 simple zones with auto-detection
# ══════════════════════════════════════════════════════════════════════════════
st.markdown('<div class="sec">📄 API Data</div>', unsafe_allow_html=True)
api_files = st.file_uploader("API order export (CSV/XLSX)", accept_multiple_files=True,
                              type=["csv","xlsx","xls"], key="api")

col1, col2 = st.columns(2)
with col1:
    st.markdown('<div class="sec">🔷 Phase 1 — Bank / Orchestrator Files</div>', unsafe_allow_html=True)
    st.caption("Bridgerpay, Payprocc, Coinsbuy, ZEN, Confirmo, TC Pay")
    p1_files = st.file_uploader("Drop all Phase 1 files", accept_multiple_files=True,
                                 type=["csv","xlsx","xls"], key="p1_bulk")
with col2:
    st.markdown('<div class="sec">📂 Phase 2 — PSP Statement Files</div>', unsafe_allow_html=True)
    st.caption("Nuvei, Axcess, Trust Payment, Payabl, Paysafe, DLocal, Skrill, PayPal, Unlimit")
    p2_files = st.file_uploader("Drop all Phase 2 files", accept_multiple_files=True,
                                 type=["csv","xlsx","xls"], key="p2_bulk")

# ── Auto-detect and show results ──────────────────────────────────────────────
bank_map = {"bridgerpay":[],"payprocc":[],"coinsbuy":[],"zen":[],"confirmo":[],"tcpay":[]}
psp_map  = {}
undetected_p1 = []
undetected_p2 = []

if p1_files:
    tags = []
    for f in p1_files:
        key = detect_phase1(f.name)
        if key:
            bank_map[key].append(f)
            tags.append(f'<span class="det-tag det-ok">{BANK_LABELS.get(key,key)}: {f.name}</span>')
        else:
            undetected_p1.append(f.name)
            tags.append(f'<span class="det-tag det-warn">❓ {f.name}</span>')
    with col1:
        st.markdown(" ".join(tags), unsafe_allow_html=True)
        if undetected_p1:
            st.warning(f"Could not detect: {', '.join(undetected_p1)}")

if p2_files:
    tags = []
    for f in p2_files:
        key = detect_phase2(f.name)
        if key:
            psp_map.setdefault(key, []).append(f)
            tags.append(f'<span class="det-tag det-ok">{PSP_LABELS.get(key,key)}: {f.name}</span>')
        else:
            undetected_p2.append(f.name)
            tags.append(f'<span class="det-tag det-warn">❓ {f.name}</span>')
    with col2:
        st.markdown(" ".join(tags), unsafe_allow_html=True)
        if undetected_p2:
            st.warning(f"Could not detect: {', '.join(undetected_p2)}")

# Confirmo: same file for Phase 1 + Phase 2
if bank_map.get("confirmo"):
    psp_map["confirmo_bp"] = bank_map["confirmo"]
# ZEN also goes through BP — use Phase 1 ZEN files for Phase 2 matching
if bank_map.get("zen"):
    psp_map["zen_bp"] = bank_map["zen"]

# Paysafe: same file for BP + PP scopes
if "paysafe" in psp_map:
    psp_map["paysafe_bp"] = psp_map["paysafe"]
    psp_map["paysafe_pp"] = psp_map["paysafe"]

uploaded_banks = [k for k,v in bank_map.items() if v]
uploaded_psps  = [k for k,v in psp_map.items() if v]

# ══════════════════════════════════════════════════════════════════════════════
# RUN
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("---")

if not api_files:
    st.info("👆 Upload the API file to begin.")
else:
    c1,c2,c3 = st.columns(3)
    with c1: st.success(f"✅ API: {len(api_files)} file(s)")
    with c2: (st.success if uploaded_banks else st.warning)(
        f"{'✅' if uploaded_banks else '⚠️'} Phase 1: {len(uploaded_banks)} bank(s) detected")
    with c3: (st.success if uploaded_psps else st.caption)(
        f"✅ Phase 2: {len(uploaded_psps)} PSP(s) detected" if uploaded_psps else "No Phase 2 files")

    run_btn = st.button("🚀 Run Reconciliation", type="primary",
                         use_container_width=True, disabled=not bool(uploaded_banks))

    if run_btn:
        for k in ["results","out_files","run_done","api_df","dup_df","dup_detail",
                  "dl_order_wise","dl_discrepancy","dl_comparison","dl_psp_revenue","dl_recon_gap","dl_free_report","free_df"]:
            st.session_state[k] = None
        st.session_state.run_done = False

        pb = st.progress(0,"Starting…"); stx = st.empty()
        def upd(p,m): pb.progress(p,text=m); stx.caption(m)

        try:
            from engine.loader import concat_files, normalize, find_col, to_numeric_col, trim_columns
            from engine.phase1 import reconcile_all
            from engine.phase2 import reconcile_phase2
            from engine.writer import write_outputs
            import gc

            upd(5,"Loading API…")
            api_df = normalize(concat_files(api_files, file_type="api"))
            gc.collect()

            sc = find_col(api_df,["Status","status"])
            dc = find_col(api_df,["Created At","CreatedAt"])
            tc = find_col(api_df,["Transaction ID","TransactionID"])
            api_en = api_df[api_df[sc].astype(str).str.lower()=="enabled"].copy() if sc else api_df.copy()
            if dc:
                api_en[dc] = pd.to_datetime(api_en[dc],errors="coerce")
                api_en = api_en[(api_en[dc]>=pd.Timestamp(start_date)) &
                                (api_en[dc]<=pd.Timestamp(end_date)+pd.Timedelta(days=1))]
            if min_amount > 0:
                g2 = find_col(api_en,["Grand Total","GrandTotal"])
                if g2: api_en=api_en[pd.to_numeric(api_en[g2],errors="coerce").fillna(0)>=min_amount]

            # ── Exclude free accounts from reconciliation ─────────────────
            FREE_TYPES = [
                "100% discount account",
                "free account",
                "free account (affiliate/partners)",
                "internal testing",
                "competition free account",
                "giveaway account (external)",
            ]
            # Search multiple columns for free account indicators
            free_mask = pd.Series(False, index=api_en.index)
            for col_name in ["Gateway","gateway","Account Type","AccountType",
                             "Order Type","OrderType","Plan Type","PlanType"]:
                col = find_col(api_en, [col_name])
                if col:
                    free_mask = free_mask | api_en[col].astype(str).str.lower().str.strip().isin(FREE_TYPES)

            free_df = api_en[free_mask].copy()
            api_en  = api_en[~free_mask].copy()

            if not free_df.empty:
                st.session_state.free_df = free_df
            else:
                st.session_state.free_df = pd.DataFrame()
            upd(15,f"API: {len(api_en):,} orders")
            del api_df; gc.collect()

            if detect_dupes and tc:
                dup_mask = api_en.duplicated(subset=tc,keep=False)
                dup_rows = api_en[dup_mask].copy()
                if not dup_rows.empty:
                    g2 = find_col(dup_rows,["Grand Total","GrandTotal"])
                    if g2: dup_rows["_gt"]=pd.to_numeric(dup_rows[g2],errors="coerce")
                    agg_dict = {"Count":(tc,"count")}
                    if g2:
                        agg_dict["Total Amount"]=("_gt","sum")
                        agg_dict["Min Amount"]=("_gt","min")
                        agg_dict["Max Amount"]=("_gt","max")
                    dup_summary = dup_rows.groupby(tc).agg(**agg_dict).reset_index()
                    dup_summary["Same Amount?"] = dup_summary.apply(
                        lambda r: "✅ Yes" if r.get("Min Amount",0)==r.get("Max Amount",0) else "❌ No", axis=1)
                    dup_summary = dup_summary.sort_values("Count",ascending=False)
                    st.session_state.dup_df = dup_summary

                    detail_cols = [c for c in [tc,g2,
                        find_col(dup_rows,["Created At","CreatedAt"]),
                        find_col(dup_rows,["Plan Type","PlanType"]),
                        find_col(dup_rows,["Order ID","OrderID"]),
                        find_col(dup_rows,["Customer Email","CustomerEmail"]),
                        find_col(dup_rows,["Gateway","gateway"]),
                        find_col(dup_rows,["Status","status"]),
                    ] if c and c in dup_rows.columns]
                    st.session_state.dup_detail = dup_rows[detail_cols].sort_values(tc)

                    unique_dups = len(dup_summary)
                    total_dup_rows = len(dup_rows)
                    dup_amt = dup_rows["_gt"].sum() if g2 else 0
                    st.warning(f"⚠️ {total_dup_rows:,} rows with {unique_dups:,} duplicate TxIDs (${dup_amt:,.0f})")

            results, errors = reconcile_all(api_en,bank_map,tol_usd=tol_usd,tol_usdt=tol_usdt,progress_cb=upd)
            gc.collect()
            for bk,err in errors.items(): st.warning(f"⚠️ {bk}: {err}")

            if uploaded_psps:
                upd(80,"Phase 2…")
                p2r,p2e = reconcile_phase2(results,psp_map,tol_usd=tol_usd)
                for bk,err in p2e.items(): st.warning(f"⚠️ Phase 2 {bk}: {err}")
                results["phase2"] = p2r

            upd(88,"Generating outputs…")
            # Cross-reference duplicates with Orchestrator/PSP results
            if st.session_state.dup_df is not None and tc:
                try:
                    dup_summary = st.session_state.dup_df
                    orch_ids = set()
                    combined = results.get("combined", pd.DataFrame())
                    if not combined.empty:
                        ctid = find_col(combined, ["Transaction ID","TransactionID"])
                        ctrk = find_col(combined, ["Tracking ID","TrackingID"])
                        if ctid: orch_ids.update(combined[ctid].dropna().tolist())
                        if ctrk: orch_ids.update(combined[ctrk].dropna().tolist())
                    psp_ids = set()
                    for pkey, pdf in results.get("phase2", {}).items():
                        if isinstance(pdf, pd.DataFrame) and not pdf.empty:
                            atid = find_col(pdf, ["_api_tid","merchantOrderId","Merchant Order ID",
                                                   "Payment Public ID"])
                            if atid: psp_ids.update(pdf[atid].dropna().astype(str).tolist())
                    def _source(tid):
                        in_orch = tid in orch_ids
                        in_psp  = tid in psp_ids
                        if in_orch and in_psp: return "API + Orch + PSP"
                        elif in_orch:          return "API + Orch"
                        elif in_psp:           return "API + PSP"
                        else:                  return "API Only"
                    dup_summary["Source"] = dup_summary[tc].apply(_source)
                    st.session_state.dup_df = dup_summary
                except Exception: pass
            out_files = write_outputs(results,api_en,start_date,end_date,out_fmt)
            gc.collect()
            st.session_state.results = results
            st.session_state.out_files = out_files
            st.session_state.api_df = api_en

            upd(92,"Building reports…")
            try:
                from engine.report_summary import write_full_summary
                b=write_full_summary(api_en,results,start_date,end_date)
                st.session_state.dl_order_wise = b.getvalue() if b else None
            except Exception as e:
                import traceback
                st.warning(f"⚠️ Order Wise: {e}")
                st.code(traceback.format_exc())
            try:
                from engine.report_mismatch import write_mismatch_excel
                b=write_mismatch_excel(results,api_en,start_date,end_date)
                st.session_state.dl_discrepancy = b.getvalue() if b else None
            except Exception as e: st.warning(f"⚠️ Discrepancy: {e}")
            try:
                from engine.report_order_wise import write_order_wise_excel
                b=write_order_wise_excel(api_en,results,results.get("phase2",{}))
                st.session_state.dl_comparison = b.getvalue() if b else None
            except Exception as e: st.warning(f"⚠️ Comparison: {e}")
            try:
                from engine.report_psp_revenue import write_psp_revenue_excel
                b=write_psp_revenue_excel(api_en,results,start_date,end_date)
                st.session_state.dl_psp_revenue = b.getvalue() if b else None
            except Exception as e: st.warning(f"⚠️ PSP Revenue: {e}")
            try:
                from engine.report_recon_gap import build_recon_gap_report
                b=build_recon_gap_report(api_en,results,start_date,end_date)
                st.session_state.dl_recon_gap = b.getvalue() if b else None
            except Exception as e: st.warning(f"⚠️ Recon Gap: {e}")
            # Free Account Report
            try:
                free_df = st.session_state.get("free_df", pd.DataFrame())
                if not free_df.empty:
                    fb = io.BytesIO()
                    with pd.ExcelWriter(fb, engine="xlsxwriter") as writer:
                        # Sheet 1: All free account records
                        keep = [c for c in [
                            find_col(free_df,["Transaction ID","TransactionID"]),
                            find_col(free_df,["Tracking ID","TrackingID"]),
                            find_col(free_df,["Order ID","OrderID"]),
                            find_col(free_df,["Grand Total","GrandTotal"]),
                            find_col(free_df,["Plan Type","PlanType"]),
                            find_col(free_df,["Gateway","gateway"]),
                            find_col(free_df,["Account Type","AccountType"]),
                            find_col(free_df,["Order Type","OrderType"]),
                            find_col(free_df,["Customer Email","CustomerEmail"]),
                            find_col(free_df,["Created At","CreatedAt"]),
                            find_col(free_df,["Status","status"]),
                        ] if c and c in free_df.columns]
                        free_df[keep].to_excel(writer, sheet_name="All Free Accounts", index=False)
                        # Sheet 2: Count by type
                        for col_name in ["Gateway","Account Type","Order Type","Plan Type"]:
                            col = find_col(free_df, [col_name])
                            if col:
                                vc = free_df[col].value_counts().reset_index()
                                vc.columns = ["Type", "Count"]
                                vc.loc[len(vc)] = ["TOTAL", vc["Count"].sum()]
                                vc.to_excel(writer, sheet_name="Count by Type", index=False)
                                break
                    fb.seek(0)
                    st.session_state.dl_free_report = fb.getvalue()
            except Exception as e: st.warning(f"⚠️ Free Account Report: {e}")

            st.session_state.run_done = True
            pb.progress(100,"✅ Complete!"); stx.empty()

        except Exception as exc:
            pb.progress(0,"Error")
            st.error(f"❌ {exc}")
            with st.expander("Traceback"): st.code(traceback.format_exc())

# ══════════════════════════════════════════════════════════════════════════════
# RESULTS
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.run_done and st.session_state.results:
    results = st.session_state.results
    api_en  = st.session_state.api_df if st.session_state.api_df is not None else pd.DataFrame()

    st.success("🎉 Done! Download reports from the sidebar ←")

    dup_df = st.session_state.dup_df
    if dup_df is not None and not dup_df.empty:
        unique_dups = len(dup_df)
        total_rows  = int(dup_df["Count"].sum())
        st.markdown('<div class="sec">🔍 Duplicate Transaction IDs</div>', unsafe_allow_html=True)
        with st.expander(f"⚠️ {unique_dups:,} duplicate TxIDs found ({total_rows:,} rows) — click to view", expanded=True):
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Unique Duplicate IDs", f"{unique_dups:,}")
            c2.metric("Total Duplicate Rows", f"{total_rows:,}")
            same_amt = (dup_df["Same Amount?"]=="✅ Yes").sum() if "Same Amount?" in dup_df.columns else 0
            c3.metric("Same Amount", f"{same_amt:,}", help="Exact duplicates")
            c4.metric("Diff Amount", f"{unique_dups - same_amt:,}", help="Same TxID, different amount")

            # Source breakdown
            if "Source" in dup_df.columns:
                st.markdown("**Where were duplicates found?**")
                src_counts = dup_df["Source"].value_counts()
                sc1,sc2,sc3,sc4 = st.columns(4)
                sc1.metric("API Only", f"{src_counts.get('API Only',0):,}",
                           help="Duplicate in API but not in Orchestrator or PSP")
                sc2.metric("API + Orch", f"{src_counts.get('API + Orch',0):,}",
                           help="Duplicate exists in both API and Orchestrator")
                sc3.metric("API + Orch + PSP", f"{src_counts.get('API + Orch + PSP',0):,}",
                           help="Duplicate exists in API, Orchestrator, and PSP")
                sc4.metric("API + PSP", f"{src_counts.get('API + PSP',0):,}",
                           help="Duplicate in API and PSP but not Orchestrator")

            st.dataframe(dup_df, use_container_width=True, hide_index=True)

            dup_detail = st.session_state.get("dup_detail")
            if dup_detail is not None and not dup_detail.empty:
                if st.checkbox("Show all duplicate rows (detail)", key="show_dup_detail"):
                    st.dataframe(dup_detail, use_container_width=True, hide_index=True)

            # Download duplicate report
            try:
                dup_buf = io.BytesIO()
                with pd.ExcelWriter(dup_buf, engine="xlsxwriter") as writer:
                    dup_df.to_excel(writer, sheet_name="Summary", index=False)
                    if dup_detail is not None and not dup_detail.empty:
                        dup_detail.to_excel(writer, sheet_name="Detail", index=False)
                    if "Source" in dup_df.columns:
                        src = dup_df["Source"].value_counts().reset_index()
                        src.columns = ["Source","Count"]
                        src.to_excel(writer, sheet_name="Source Breakdown", index=False)
                dup_buf.seek(0)
                st.download_button("⬇️ Download Duplicate TxID Report",
                    data=dup_buf.getvalue(),
                    file_name=f"FN_Duplicate_TxIDs_{start_date}_{end_date}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True, key="dl_dup_report")
            except Exception as e:
                st.warning(f"⚠️ Duplicate report: {e}")

    try:
        from engine.report_summary import compute_summary_stats
        s = compute_summary_stats(api_en, results)
        if s:
            k1,k2,k3,k4,k5=st.columns(5)
            op=s["orch_orders"]/max(s["api_orders"],1)*100
            pp=s["psp_orders"]/max(s["api_orders"],1)*100
            def kpi(col,val,lbl,sub,clr):
                col.markdown(f'<div class="kpi-card"><div class="kpi-val" style="color:{clr}">'
                    f'{val}</div><div class="kpi-lbl">{lbl}</div>'
                    f'<div class="kpi-sub" style="color:{clr}">{sub}</div></div>',
                    unsafe_allow_html=True)
            kpi(k1,f"{s['api_orders']:,}","API Orders",f"${s['api_rev']:,.0f}","#0a1628")
            kpi(k2,f"{s['orch_orders']:,}","Orchestrator",f"{op:.1f}% · ${s['orch_rev']:,.0f}","#00875a" if op>=95 else "#e65100")
            kpi(k3,f"{s['psp_orders']:,}","PSP Reconciled",f"{pp:.1f}% · ${s['psp_rev']:,.0f}","#00875a" if pp>=95 else "#e65100")
            kpi(k4,f"{s['diff_orch']:,}","Diff (Orch)",f"${s['diff_orch_rev']:,.0f}","#e53935")
            kpi(k5,f"{s['diff_psp']:,}","Diff (PSP)",f"${s['diff_psp_rev']:,.0f}","#e53935")
            # Free account excluded info
            free_df = st.session_state.get("free_df", pd.DataFrame())
            if not free_df.empty:
                st.info(f"ℹ️ {len(free_df):,} free accounts excluded from reconciliation (100% Discount, Free Account, Internal Testing, etc.)")
            st.markdown("")
    except Exception as _e:
        st.warning(f"KPI: {_e}")

    def _n(v):
        try: return f"{int(v):,}"
        except: return str(v)
    def _a(v):
        try: return f"${float(v):,.2f}"
        except: return str(v)
    def _pct(v):
        try:
            p=float(v); c="ok" if p>=95 else "pa" if p>=85 else "lo"
            return f'<span class="{c}">{p:.1f}%</span>'
        except: return str(v)

    try:
        from engine.report_phase_summary import build_phase1_summary, build_phase2_summary
        p1r = build_phase1_summary(api_en, results)
        p2r = build_phase2_summary(results)

        def _tbl(rows, hdrs):
            hh="".join(f"<th>{h}</th>" for h in hdrs)
            return f'<div style="overflow-x:auto"><table class="rt"><thead><tr>{hh}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'

        h1=["Bank","API Qty","API Amt","Bank Qty","Bank Amt","Mismatch Qty",
            "Mismatch Amt","Not in Bank Qty","Not in Bank Amt","Extra Qty","Extra Amt","Match %"]
        rows1=[f'<tr><td class="nm">{r["Bank Name"]}</td>'
            f'<td>{_n(r["API Qty"])}</td><td>{_a(r["API Amt"])}</td>'
            f'<td>{_n(r["Bank Qty"])}</td><td>{_a(r["Bank Amt"])}</td>'
            f'<td class="d">{_n(r["Mismatch Qty"])}</td><td class="d">{_a(r["Mismatch Amt"])}</td>'
            f'<td class="d">{_n(r["Not in Bank Qty"])}</td><td class="d">{_a(r["Not in Bank Amt"])}</td>'
            f'<td>{_n(r["Extra in Bank Qty"])}</td><td>{_a(r["Extra in Bank Amt"])}</td>'
            f'<td>{_pct(r["Match %"])}</td></tr>' for r in p1r]
        st.markdown('<div class="sec">Phase 1 — API vs Bank</div>', unsafe_allow_html=True)
        st.markdown(_tbl(rows1,h1), unsafe_allow_html=True)

        if results.get("phase2"):
            h2=["PSP","Orch Qty","Orch Amt","PSP Qty","PSP Amt","Mismatch Qty",
                "Mismatch Amt","Not in PSP Qty","Not in PSP Amt","Extra Qty","Extra Amt","Match %"]
            rows2=[f'<tr><td class="nm">{r["PSP Name"]}</td>'
                f'<td>{_n(r["Orchestrator Qty"])}</td><td>{_a(r["Orchestrator Amt"])}</td>'
                f'<td>{_n(r["PSP Qty"])}</td><td>{_a(r["PSP Amt"])}</td>'
                f'<td class="d">{_n(r["Mismatch Qty"])}</td><td class="d">{_a(r["Mismatch Amt"])}</td>'
                f'<td class="d">{_n(r["Not in PSP Qty"])}</td><td class="d">{_a(r["Not in PSP Amt"])}</td>'
                f'<td>{_n(r["Extra in PSP Qty"])}</td><td>{_a(r["Extra in PSP Amt"])}</td>'
                f'<td>{_pct(r["Match %"])}</td></tr>' for r in p2r]
            st.markdown('<div class="sec">Phase 2 — Orchestrator vs PSP</div>', unsafe_allow_html=True)
            st.markdown(_tbl(rows2,h2), unsafe_allow_html=True)
    except Exception as _e:
        st.warning(f"Tables: {_e}")

    # Downloads at bottom
    st.markdown("---")
    d1,d2,d3,d4,d5,d6 = st.columns(6)
    for col,key,lbl,fname in [
        (d1,"dl_order_wise","⬇️ Order Wise",f"FN_OrderWise_{start_date}_{end_date}.xlsx"),
        (d2,"dl_discrepancy","⬇️ Discrepancy",f"FN_Discrepancy_{start_date}_{end_date}.xlsx"),
        (d3,"dl_comparison","⬇️ Comparison",f"FN_Comparison_{p1_start}_{p2_end}.xlsx"),
        (d4,"dl_psp_revenue","⬇️ PSP Revenue",f"FN_PSP_Revenue_{start_date}_{end_date}.xlsx"),
        (d5,"dl_recon_gap","⬇️ Recon Gap",f"FN_Recon_Gap_{start_date}_{end_date}.xlsx"),
        (d6,"dl_free_report","⬇️ Free Accounts",f"FN_Free_Accounts_{start_date}_{end_date}.xlsx"),
    ]:
        data = st.session_state.get(key)
        if data:
            col.download_button(lbl,data=data,file_name=fname,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,key=f"main_{key}")
        else:
            col.caption(f"{lbl} — not available")
