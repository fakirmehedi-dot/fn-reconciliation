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
</style>""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────────────
for k,v in [("results",None),("out_files",None),("run_done",False),
            ("api_df",None),("dup_df",None),
            ("dl_order_wise",None),("dl_discrepancy",None),("dl_comparison",None)]:
    if k not in st.session_state: st.session_state[k] = v

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Settings")
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
        st.caption(f"{p1_start} → {p1_end}")
        st.markdown("**📅 Period 2**")
        c3,c4=st.columns(2)
        with c3: p2_start=st.date_input("P2 From",value=datetime.date(2026,4,1),key="p2s",label_visibility="collapsed")
        with c4: p2_end  =st.date_input("P2 To",value=datetime.date(2026,4,21),key="p2e",label_visibility="collapsed")
        st.caption(f"{p2_start} → {p2_end}")
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

    # Download Center
    if st.session_state.run_done:
        st.markdown("---")
        st.markdown("### 📥 Downloads")
        for key,label,fname in [
            ("dl_order_wise","⬇️ Order Wise",f"FN_OrderWise_{start_date}_{end_date}.xlsx"),
            ("dl_discrepancy","⬇️ Discrepancy",f"FN_Discrepancy_{start_date}_{end_date}.xlsx"),
            ("dl_comparison","⬇️ Comparison",f"FN_Comparison_{p1_start}_{p2_end}.xlsx"),
        ]:
            data = st.session_state.get(key)
            if data:
                st.download_button(label,data=data,file_name=fname,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,key=f"sb_{key}")
        out_files = st.session_state.out_files or {}
        if out_files:
            zb = io.BytesIO()
            with zipfile.ZipFile(zb,"w",zipfile.ZIP_DEFLATED) as zf:
                for fn,fb in out_files.items():
                    fb.seek(0); zf.writestr(fn,fb.read())
            zb.seek(0)
            st.download_button("⬇️ All (ZIP)",data=zb.getvalue(),
                file_name=f"FN_Recon_{start_date}_{end_date}.zip",
                mime="application/zip",use_container_width=True,key="sb_zip")

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
# UPLOAD — all on one page, no tabs
# ══════════════════════════════════════════════════════════════════════════════
st.markdown('<div class="sec">📄 API Data</div>', unsafe_allow_html=True)
api_files = st.file_uploader("API CSV/XLSX", accept_multiple_files=True,
                              type=["csv","xlsx","xls"], key="api")

c1, c2 = st.columns(2)
with c1:
    st.markdown('<div class="sec">🔷 Phase 1 — Banks</div>', unsafe_allow_html=True)
    bp_files  = st.file_uploader("Bridgerpay",  accept_multiple_files=True,type=["csv","xlsx","xls"],key="bp")
    pp_files  = st.file_uploader("Payprocc",    accept_multiple_files=True,type=["csv","xlsx","xls"],key="pp")
    cb_files  = st.file_uploader("Coinsbuy (API only)",accept_multiple_files=True,type=["csv","xlsx","xls"],key="cb")
with c2:
    st.markdown('<div class="sec">🟢 Phase 1 — Independent PSPs</div>', unsafe_allow_html=True)
    zen_files = st.file_uploader("ZEN",         accept_multiple_files=True,type=["csv","xlsx","xls"],key="zen")
    cfm_files = st.file_uploader("Confirmo (API + Orch)",accept_multiple_files=True,type=["csv","xlsx","xls"],key="cfm")
    tcp_files = st.file_uploader("TC Pay",      accept_multiple_files=True,type=["csv","xlsx","xls"],key="tcp")

with st.expander("📂 Phase 2 — PSP Statements (click to expand)"):
    p1c, p2c, p3c = st.columns(3)
    with p1c:
        nuvni_f  = st.file_uploader("Nuvei NI",      type=["csv","xlsx","xls"],key="p2_ni")
        nuvaq_f  = st.file_uploader("Nuvei AQ",      type=["csv","xlsx","xls"],key="p2_aq")
        axcess_f = st.file_uploader("Axcess/Truevo", type=["csv","xlsx","xls"],key="p2_ax")
        paypal_f = st.file_uploader("PayPal",        type=["csv","xlsx","xls"],key="p2_pp")
    with p2c:
        trustp_f = st.file_uploader("Trust Payment", type=["csv","xlsx","xls"],key="p2_tp")
        payabl_f = st.file_uploader("Payabl",        type=["csv","xlsx","xls"],key="p2_pa")
        paysfe_f = st.file_uploader("Paysafe",       type=["csv","xlsx","xls"],key="p2_ps")
        unlimit_f= st.file_uploader("Unlimit",       type=["csv","xlsx","xls"],key="p2_ul")
    with p3c:
        dloc_f   = st.file_uploader("DLocal",        type=["csv","xlsx","xls"],key="p2_dl")
        skrill_f = st.file_uploader("Skrill",        type=["csv","xlsx","xls"],key="p2_sk")
        pspp_f   = st.file_uploader("Paysafe PP",    type=["csv","xlsx","xls"],key="p2_spp")

# ══════════════════════════════════════════════════════════════════════════════
# RUN
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("---")

bank_map = {"bridgerpay":bp_files or [],"payprocc":pp_files or [],
            "coinsbuy":cb_files or [],"zen":zen_files or [],
            "confirmo":cfm_files or [],"tcpay":tcp_files or []}
psp_map  = {k:[v] if v else [] for k,v in {
            "paypal":paypal_f,"unlimit":unlimit_f,"nuvei_ni":nuvni_f,
            "nuvei_aq":nuvaq_f,"axcess":axcess_f,
            "trustpay":trustp_f,"payabl":payabl_f,"paysafe_bp":paysfe_f,
            "dlocal":dloc_f,"skrill":skrill_f,"paysafe_pp":pspp_f}.items()}
psp_map["confirmo_bp"] = cfm_files or []

uploaded_banks = [k for k,v in bank_map.items() if v]
uploaded_psps  = [k for k,v in psp_map.items() if v]

if not api_files:
    st.info("👆 Upload the API file above to begin.")
else:
    c1,c2,c3 = st.columns(3)
    with c1: st.success(f"✅ API: {len(api_files)} file(s)")
    with c2: (st.success if uploaded_banks else st.warning)(
        f"{'✅' if uploaded_banks else '⚠️'} Banks: {len(uploaded_banks)}")
    with c3: (st.info if uploaded_psps else st.caption)(
        f"📂 PSPs: {len(uploaded_psps)}" if uploaded_psps else "No PSP files")

    run_btn = st.button("🚀 Run Reconciliation", type="primary",
                         use_container_width=True, disabled=not bool(uploaded_banks))

    if run_btn:
        for k in ["results","out_files","run_done","api_df","dup_df",
                  "dl_order_wise","dl_discrepancy","dl_comparison"]:
            st.session_state[k] = None
        st.session_state.run_done = False

        pb = st.progress(0,"Starting…"); stx = st.empty()
        def upd(p,m): pb.progress(p,text=m); stx.caption(m)

        try:
            from engine.loader import concat_files, normalize, find_col, to_numeric_col
            from engine.phase1 import reconcile_all
            from engine.phase2 import reconcile_phase2
            from engine.writer import write_outputs

            upd(5,"Loading API…")
            api_df = normalize(concat_files(api_files))
            # Memory optimization: keep only needed columns
            from engine.loader import trim_columns
            api_df = trim_columns(api_df, "api")
            import gc; gc.collect()
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
            upd(15,f"API: {len(api_en):,} orders")

            if detect_dupes and tc:
                dup_mask = api_en.duplicated(subset=tc,keep=False)
                dup_rows = api_en[dup_mask]
                if not dup_rows.empty:
                    g2 = find_col(dup_rows,["Grand Total","GrandTotal"])
                    dr = dup_rows.copy()
                    if g2: dr["_gt"]=pd.to_numeric(dr[g2],errors="coerce")
                    agg={"Count":(tc,"count")}
                    if g2: agg["Total Amount"]=("_gt","sum")
                    st.session_state.dup_df = dr.groupby(tc).agg(**agg).reset_index()
                    st.warning(f"⚠️ {len(dup_rows):,} rows with duplicate TxIDs")

            results, errors = reconcile_all(api_en,bank_map,tol_usd=tol_usd,tol_usdt=tol_usdt,progress_cb=upd)
            for bk,err in errors.items(): st.warning(f"⚠️ {bk}: {err}")

            if uploaded_psps:
                upd(80,"Phase 2…")
                p2r,p2e = reconcile_phase2(results,psp_map,tol_usd=tol_usd)
                for bk,err in p2e.items(): st.warning(f"⚠️ Phase 2 {bk}: {err}")
                results["phase2"] = p2r

            upd(88,"Generating outputs…")
            out_files = write_outputs(results,api_en,start_date,end_date,out_fmt)
            st.session_state.results = results
            st.session_state.out_files = out_files
            st.session_state.api_df = api_en

            upd(92,"Building reports…")
            try:
                from engine.report_summary import write_full_summary
                b=write_full_summary(api_en,results,start_date,end_date)
                st.session_state.dl_order_wise = b.getvalue() if b else None
            except: pass
            try:
                from engine.report_mismatch import write_mismatch_excel
                b=write_mismatch_excel(results,api_en,start_date,end_date)
                st.session_state.dl_discrepancy = b.getvalue() if b else None
            except: pass
            try:
                from engine.report_order_wise import write_order_wise_excel
                b=write_order_wise_excel(api_en,results,results.get("phase2",{}))
                st.session_state.dl_comparison = b.getvalue() if b else None
            except: pass

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
        with st.expander(f"🔍 Duplicate TxIDs — {len(dup_df):,}"):
            st.dataframe(dup_df, use_container_width=True, hide_index=True)

    # KPI
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
            kpi(k2,f"{s['orch_orders']:,}","Orchestrator",f"{op:.1f}%","#00875a" if op>=95 else "#e65100")
            kpi(k3,f"{s['psp_orders']:,}","PSP Reconciled",f"{pp:.1f}%","#00875a" if pp>=95 else "#e65100")
            kpi(k4,f"{s['diff_orch']:,}","Diff (Orch)",f"${s['diff_orch_rev']:,.0f}","#e53935")
            kpi(k5,f"{s['diff_psp']:,}","Diff (PSP)",f"${s['diff_psp_rev']:,.0f}","#e53935")
            st.markdown("")
    except Exception as _e:
        st.warning(f"KPI: {_e}")

    # Phase tables
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

    # Quick downloads at bottom
    st.markdown("---")
    d1,d2,d3 = st.columns(3)
    for col,key,lbl,fname in [
        (d1,"dl_order_wise","⬇️ Order Wise",f"FN_OrderWise_{start_date}_{end_date}.xlsx"),
        (d2,"dl_discrepancy","⬇️ Discrepancy",f"FN_Discrepancy_{start_date}_{end_date}.xlsx"),
        (d3,"dl_comparison","⬇️ Comparison",f"FN_Comparison_{p1_start}_{p2_end}.xlsx"),
    ]:
        data = st.session_state.get(key)
        if data:
            col.download_button(lbl,data=data,file_name=fname,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,key=f"main_{key}")
        else:
            col.caption(f"{lbl} — not available")
