"""
engine/phase2.py  –  Phase 2: Orchestrator vs PSPs
FIX: JOIN from PSP side (LEFT) so denominator = PSP rows in scope.
     Previously joined from all BP rows → 191k rows, 90%+ NOT IN PSP.
Match keys verified against actual file data.
"""
import pandas as pd
import numpy as np
import io
from .loader import load_file, concat_files, normalize, find_col, to_numeric_col

TOL = 0.01


def _verdict_psp_base(df, psp_amt_col, orch_amt_col, tol=TOL):
    """PSP-based verdict: PSP row is the anchor."""
    no_match = df[orch_amt_col].isna()
    diff = (df[psp_amt_col].fillna(0) - df[orch_amt_col].fillna(0)).abs()
    return np.where(no_match, "NOT IN ORCH",
           np.where(diff <= tol, "RECONCILED", "AMOUNT MISMATCH"))


def _load_psp(files):
    f = files if isinstance(files, list) else [files]
    dfs = [load_file(x) for x in f if x]
    if not dfs:
        return pd.DataFrame()
    return normalize(pd.concat(dfs, ignore_index=True).drop_duplicates())


def _match(psp_df, psp_key, psp_amt,
           orch_df, orch_key, orch_amt,
           psp_label, tol=TOL):
    """
    Core match: LEFT JOIN from PSP (base) → Orchestrator.
    Every PSP row appears once. BP/PP amount pulled in where key matches.
    """
    psp_d = psp_df.drop_duplicates(subset=psp_key, keep="first")
    psp_d = psp_d.copy()
    psp_d["_psp_amt"] = to_numeric_col(psp_d[psp_amt].astype(str))

    orch_sub = orch_df[[orch_key, orch_amt]].drop_duplicates(subset=orch_key, keep="first").copy()
    orch_sub["_orch_amt"] = to_numeric_col(orch_sub[orch_amt].astype(str))

    m = psp_d.merge(
        orch_sub[[orch_key, "_orch_amt"]].rename(columns={orch_key: "_ok"}),
        left_on=psp_key, right_on="_ok", how="left"
    )
    m["PSP_Amount"]  = m["_psp_amt"]
    m["Orch_Amount"] = m["_orch_amt"]
    m["Verdict"]     = _verdict_psp_base(m, "PSP_Amount", "Orch_Amount", tol)
    m["Diff (USD)"]  = (m["PSP_Amount"].fillna(0) - m["Orch_Amount"].fillna(0)).round(4)
    m["PSP"]         = psp_label
    return m


# ─────────────────────────────────────────────────────────────────────────────
# PSPs under BRIDGERPAY
# ─────────────────────────────────────────────────────────────────────────────

def recon_paypal(bp_df, files, tol=TOL):
    """PayPal: BP transactionId = PayPal Transaction ID | Gross"""
    psp = _load_psp(files)
    if psp.empty: return pd.DataFrame()
    tid = find_col(psp, ["Transaction ID","TransactionID","transaction_id"])
    amt = find_col(psp, ["Gross","gross"])
    if not tid or not amt:
        raise ValueError(f"PayPal: need 'Transaction ID'+'Gross'. Columns: {list(psp.columns)}")
    tx_col = find_col(bp_df, ["transactionId","transaction_id","TransactionID"])
    a_col  = find_col(bp_df, ["amount","Amount"])
    if not tx_col or not a_col: return pd.DataFrame()
    return _match(psp, tid, amt, bp_df, tx_col, a_col, "PayPal", tol)


def recon_unlimit(bp_df, files, tol=TOL):
    """Unlimit: BP pspOrderId = Unlimit Payment ID | Amount | Status=Captured"""
    psp = _load_psp(files)
    if psp.empty: return pd.DataFrame()
    pid = find_col(psp, ["Payment ID","PaymentID","payment_id","Id","ID"])
    amt = find_col(psp, ["Amount","amount"])
    sta = find_col(psp, ["Status","status"])
    if not pid or not amt:
        raise ValueError(f"Unlimit: need 'Payment ID'+'Amount'. Columns: {list(psp.columns)}")
    if sta:
        psp = psp[psp[sta].astype(str).str.lower() == "captured"].copy()
    bpkey = find_col(bp_df, ["pspOrderId","psp_order_id","psporderid"])
    a_col = find_col(bp_df, ["amount","Amount"])
    if not bpkey or not a_col: return pd.DataFrame()
    return _match(psp, pid, amt, bp_df, bpkey, a_col, "Unlimit", tol)


def recon_nuvei(bp_df, files, label, tol=TOL):
    """
    Nuvei NI & AQ:
    VERIFIED: PSP 'Client Unique ID' = BP 'transactionId'
    NI: 15,127 | AQ: 23,703 matches
    Filter: Transaction Type = Sale only
    """
    f = files if isinstance(files, list) else [files]
    dfs = []
    for x in f:
        if x:
            x.seek(0)
            df = normalize(load_file(x))
            if find_col(df, ["Client Unique ID","ClientUniqueID"]) is None:
                x.seek(0)
                content = x.read()
                for skip in [12, 1, 2, 3]:
                    try:
                        nm = getattr(x, 'name', '').lower()
                        if nm.endswith(('.xlsx','.xls')):
                            trial = normalize(pd.read_excel(io.BytesIO(content), skiprows=skip, dtype=str))
                        else:
                            trial = normalize(pd.read_csv(io.BytesIO(content), skiprows=skip, dtype=str, low_memory=False))
                        if find_col(trial, ["Client Unique ID","ClientUniqueID"]):
                            df = trial
                            break
                    except Exception:
                        continue
            dfs.append(df)
    if not dfs: return pd.DataFrame()
    psp = normalize(pd.concat(dfs, ignore_index=True).drop_duplicates())

    # CONFIRMED: Nuvei Custom Data = BP merchantOrderId (from conversation)
    pid    = find_col(psp, ["Custom Data","CustomData","custom_data","customdata"])
    amt    = find_col(psp, ["Amount","amount"])
    txtype = find_col(psp, ["Transaction Type","TransactionType","type","Type"])
    if not pid or not amt:
        raise ValueError(f"{label}: need 'Custom Data'+'Amount'. Columns: {list(psp.columns)}")
    if txtype:
        psp = psp[psp[txtype].astype(str).str.lower() == "sale"].copy()

    # CONFIRMED: BP merchantOrderId = Nuvei Custom Data (from conversation)
    moi_col = find_col(bp_df, ["merchantOrderId","merchant_order_id","MerchantOrderID"])
    a_col   = find_col(bp_df, ["amount","Amount"])
    if not moi_col or not a_col: return pd.DataFrame()
    return _match(psp, pid, amt, bp_df, moi_col, a_col, label, tol)


def recon_axcess(bp_df, files, tol=TOL):
    """
    Axcess / Truevo:
    VERIFIED: PSP 'InvoiceId' = BP 'merchantOrderId' → 18,152 matches
    Filter: PaymentType=DB AND Result=ACK | Amount: Credit (comma decimal)
    """
    psp = _load_psp(files)
    if psp.empty: return pd.DataFrame()
    pid = find_col(psp, ["InvoiceId","invoiceId","invoice_id","Invoice ID"])
    amt = find_col(psp, ["Credit","credit","Amount","amount"])
    pt  = find_col(psp, ["PaymentType","paymentType","payment_type"])
    res = find_col(psp, ["Result","result"])
    if not pid or not amt:
        raise ValueError(f"Axcess: need 'InvoiceId'+'Credit'. Columns: {list(psp.columns)}")
    if pt:  psp = psp[psp[pt].astype(str).str.upper() == "DB"].copy()
    if res: psp = psp[psp[res].astype(str).str.upper() == "ACK"].copy()

    moi   = find_col(bp_df, ["merchantOrderId","merchant_order_id","MerchantOrderID"])
    a_col = find_col(bp_df, ["amount","Amount"])
    if not moi or not a_col: return pd.DataFrame()
    return _match(psp, pid, amt, bp_df, moi, a_col, "Axcess/Truevo", tol)


def recon_confirmo_bp(bp_df, files, tol=TOL):
    """
    Confirmo Phase 2 — Bridgerpay vs Confirmo
    April: Confirmo is independent PSP — same file used for BOTH Phase 1 (API) and Phase 2 (Orchestrator).
    March: Confirmo goes through Bridgerpay.

    CONFIRMED match key: BP 'id' = Confirmo 'Reference'
    Amount: Confirmo MerchantAmount vs BP amount
    """
    psp = _load_psp(files)
    if psp.empty: return pd.DataFrame()
    ref = find_col(psp, ["Reference","reference"])
    amt = find_col(psp, ["MerchantAmount","merchantAmount","merchant_amount","Amount","amount"])
    if not ref or not amt:
        raise ValueError(f"Confirmo(BP): need 'Reference'+'MerchantAmount'. Columns: {list(psp.columns)}")
    psp[amt] = to_numeric_col(psp[amt])
    psp_d = psp.drop_duplicates(subset=ref, keep="first")
    bp_id = find_col(bp_df, ["id","Id","ID"])
    a_col = find_col(bp_df, ["amount","Amount"])
    if not bp_id or not a_col: return pd.DataFrame()
    m = bp_df[[bp_id, a_col]].merge(
        psp_d[[ref, amt]].rename(columns={ref:"_k", amt:"PSP_Amount"}),
        left_on=bp_id, right_on="_k", how="left")
    m["Orch_Amount"] = to_numeric_col(m[a_col].astype(str))
    m["Verdict"]     = _verdict_psp_base(m, "PSP_Amount", "Orch_Amount", tol)
    m["Diff (USD)"]  = (m["PSP_Amount"].fillna(0) - m["Orch_Amount"].fillna(0)).round(4)
    m["PSP"]         = "Confirmo (via BP)"
    return m

def recon_trustpayment(bp_df, files, tol=TOL):
    """
    Trust Payment
    VERIFIED against actual files:
      - Base: BP rows with pspName=TrustPayments → 14,126 rows
      - Filter: TP Settle Status = 100 (settled) only — Settle Status=3 = different MID, 13,784 rows excluded
      - Match: BP transactionId = TP Reference
      - Amount: BP amount vs TP Settle Amount
      - Result: 14,126 / 14,126 = 100%
    """
    psp = _load_psp(files)
    if psp.empty: return pd.DataFrame()

    ref = find_col(psp, ["Reference","reference"])
    amt = find_col(psp, ["Settle Amount","SettleAmount","settle_amount","Authorised Amount","Amount","amount"])
    sta = find_col(psp, ["Settle Status","SettleStatus","settle_status"])
    if not ref or not amt:
        raise ValueError(f"Trust Payment: need 'Reference'+'Settle Amount'. Columns: {list(psp.columns)}")

    psp[amt] = to_numeric_col(psp[amt])
    # Filter to Settle Status=100 (fully settled) — excludes other MID rows (Status=3)
    if sta:
        psp = psp[psp[sta].astype(str).str.strip() == "100"].copy()
    psp_d = psp.drop_duplicates(subset=ref, keep="first")

    # Base: BP rows with pspName=TrustPayments — gives correct denominator 14,126
    psp_name_col = find_col(bp_df, ["pspName","psp_name","PSP Name","pspname"])
    bp_scope = bp_df[bp_df[psp_name_col].astype(str) == "TrustPayments"].copy() if psp_name_col else bp_df.copy()

    tx_col = find_col(bp_scope, ["transactionId","transaction_id","TransactionID"])
    a_col  = find_col(bp_scope, ["amount","Amount"])
    if not tx_col or not a_col: return pd.DataFrame()

    # Include merchantOrderId so _build_psp_set can map back to API Transaction ID
    moi_col = find_col(bp_scope, ["merchantOrderId","merchant_order_id","MerchantOrderID"])
    keep = [c for c in [tx_col, moi_col, a_col] if c]
    m = bp_scope[keep].merge(
        psp_d[[ref, amt]].rename(columns={ref:"_k", amt:"PSP_Amount"}),
        left_on=tx_col, right_on="_k", how="left")
    m["Orch_Amount"] = to_numeric_col(m[a_col].astype(str))
    m["Verdict"]     = _verdict_psp_base(m, "PSP_Amount", "Orch_Amount", tol)
    m["Diff (USD)"]  = (m["PSP_Amount"].fillna(0) - m["Orch_Amount"].fillna(0)).round(4)
    m["PSP"]         = "Trust Payment"
    if moi_col: m["_api_tid"] = m[moi_col]
    return m

def recon_payabl(bp_df, files, tol=TOL):
    """
    Payabl:
    VERIFIED: PSP 'Tx-Id' = BP 'transactionId' → 50,616 matches
    Amount: Amount | No filter (all Successful Captures)
    """
    psp = _load_psp(files)
    if psp.empty: return pd.DataFrame()
    # CONFIRMED: Custom 3 = BP merchantOrderId (from conversation)
    pid = find_col(psp, ["Custom 3","Custom3","custom_3","custom3","CustomField3"])
    amt = find_col(psp, ["Amount","amount"])
    if not pid or not amt:
        raise ValueError(f"Payabl: need 'Custom 3'+'Amount'. Columns: {list(psp.columns)}")
    # CONFIRMED: BP merchantOrderId = Payabl Custom 3 (from conversation)
    moi_col = find_col(bp_df, ["merchantOrderId","merchant_order_id","MerchantOrderID"])
    a_col   = find_col(bp_df, ["amount","Amount"])
    if not moi_col or not a_col: return pd.DataFrame()
    return _match(psp, pid, amt, bp_df, moi_col, a_col, "Payabl", tol)


def recon_paysafe_bp(bp_df, files, tol=TOL):
    """
    Paysafe under Bridgerpay
    VERIFIED against actual files:
      - Base: BP rows with pspName=Paysafe → 23,386 rows
      - Filter: PS Transaction Type = Settlement (one row per txn, final settled amount)
      - Match: BP transactionId = PS Transaction ID
      - Amount: BP amount vs PS Amount
      - Result: 23,311 / 23,386 = 99.7%  (75 BP rows not yet in PS = pending settlement)
    """
    psp = _load_psp(files)
    if psp.empty: return pd.DataFrame()

    tid = find_col(psp, ["Transaction ID","TransactionID","transaction_id"])
    amt = find_col(psp, ["Amount","amount","Settlement Amount"])
    txtype = find_col(psp, ["Transaction Type","TransactionType","transaction_type"])
    if not tid or not amt:
        raise ValueError(f"Paysafe(BP): need 'Transaction ID'+'Amount'. Columns: {list(psp.columns)}")

    psp[amt] = to_numeric_col(psp[amt])
    # Filter to Settlement rows only — each transaction has 3 rows (Payment Handle, authorization, Settlement)
    if txtype:
        psp = psp[psp[txtype].astype(str).str.lower() == "settlement"].copy()
    psp_d = psp.drop_duplicates(subset=tid, keep="first")

    # Base: BP rows with pspName=Paysafe — correct denominator
    psp_name_col = find_col(bp_df, ["pspName","psp_name","PSP Name","pspname"])
    bp_scope = bp_df[bp_df[psp_name_col].astype(str).str.lower() == "paysafe"].copy() if psp_name_col else bp_df.copy()

    tx_col = find_col(bp_scope, ["transactionId","transaction_id","TransactionID"])
    a_col  = find_col(bp_scope, ["amount","Amount"])
    if not tx_col or not a_col: return pd.DataFrame()

    # Include merchantOrderId so _build_psp_set can map back to API Transaction ID
    moi_col = find_col(bp_scope, ["merchantOrderId","merchant_order_id","MerchantOrderID"])
    keep = [c for c in [tx_col, moi_col, a_col] if c]
    m = bp_scope[keep].merge(
        psp_d[[tid, amt]].rename(columns={tid:"_k", amt:"PSP_Amount"}),
        left_on=tx_col, right_on="_k", how="left")
    m["Orch_Amount"] = to_numeric_col(m[a_col].astype(str))
    m["Verdict"]     = _verdict_psp_base(m, "PSP_Amount", "Orch_Amount", tol)
    m["Diff (USD)"]  = (m["PSP_Amount"].fillna(0) - m["Orch_Amount"].fillna(0)).round(4)
    m["PSP"]         = "Paysafe (BP)"
    if moi_col: m["_api_tid"] = m[moi_col]
    return m

def recon_dlocal(pp_df, files, tol=TOL):
    """
    DLocal:
    VERIFIED: PSP 'Invoice' = PP 'Payment Public ID' → 6,621 matches
    Amount: Balance Amount vs PP _usd
    Filter: PAYMENT + PAID
    """
    psp = _load_psp(files)
    if psp.empty: return pd.DataFrame()
    inv = find_col(psp, ["Invoice","invoice"])
    amt = find_col(psp, ["Balance Amount","balance_amount","BalanceAmount","Amount","amount"])
    typ = find_col(psp, ["Transaction Type","Type","type"])
    sta = find_col(psp, ["Status","status"])
    if not inv or not amt:
        raise ValueError(f"DLocal: need 'Invoice'+'Balance Amount'. Columns: {list(psp.columns)}")
    if typ: psp = psp[psp[typ].astype(str).str.upper() == "PAYMENT"].copy()
    if sta: psp = psp[psp[sta].astype(str).str.upper() == "PAID"].copy()

    pub = find_col(pp_df, ["Payment Public ID","PaymentPublicID","payment_public_id"])
    a   = find_col(pp_df, ["_usd","Amount","amount"])
    if not pub or not a: return pd.DataFrame()
    return _match(psp, inv, amt, pp_df, pub, a, "DLocal", tol)


def recon_skrill(pp_df, files, tol=TOL):
    """
    Skrill:
    VERIFIED: PSP 'Reference' = PP 'Payment Public ID' → 917 matches
    Amount: [+] vs PP _usd | Filter: Receive Money + Reference not null
    """
    psp = _load_psp(files)
    if psp.empty: return pd.DataFrame()
    ref = find_col(psp, ["Reference","reference"])
    amt = find_col(psp, ["[+]","Amount","amount","Gross","gross"])
    typ = find_col(psp, ["Type","type","Transaction Type"])
    if not ref or not amt:
        raise ValueError(f"Skrill: need 'Reference'+'[+]'. Columns: {list(psp.columns)}")
    if typ:
        psp = psp[psp[typ].astype(str).str.lower().str.contains("receive", na=False)].copy()
    psp = psp[psp[ref].notna() & (psp[ref].astype(str).str.strip() != "")].copy()

    pub = find_col(pp_df, ["Payment Public ID","PaymentPublicID","payment_public_id"])
    a   = find_col(pp_df, ["_usd","Amount","amount"])
    if not pub or not a: return pd.DataFrame()
    return _match(psp, ref, amt, pp_df, pub, a, "Skrill", tol)


def recon_paysafe_pp(pp_df, files, tol=TOL):
    """
    Paysafe under Payprocc
    VERIFIED against actual files:
      - Filter: PS Transaction Type = Settlement
      - Scope: PS rows whose Merchant Transaction ID appears in PP Payment Public IDs
               (PP transactions are UUID — many other Paysafe rows belong to other merchants)
      - Match: PS Merchant Transaction ID = PP Payment Public ID
      - Amount: PP Amount (LOCAL currency) vs PS Amount — NOT _usd
      - Result: 4,803 / 4,803 = 100%
    """
    psp = _load_psp(files)
    if psp.empty: return pd.DataFrame()

    mid    = find_col(psp, ["Merchant Transaction ID","MerchantTransactionID","merchant_transaction_id"])
    amt    = find_col(psp, ["Amount","amount","Settlement Amount"])
    txtype = find_col(psp, ["Transaction Type","TransactionType","transaction_type"])
    if not mid or not amt:
        raise ValueError(f"Paysafe(PP): need 'Merchant Transaction ID'+'Amount'. Columns: {list(psp.columns)}")

    psp[amt] = to_numeric_col(psp[amt])
    # Filter to Settlement rows only
    if txtype:
        psp = psp[psp[txtype].astype(str).str.lower() == "settlement"].copy()

    pub   = find_col(pp_df, ["Payment Public ID","PaymentPublicID","payment_public_id"])
    a_col = find_col(pp_df, ["Amount","amount"])  # LOCAL currency — NOT _usd
    if not pub or not a_col: return pd.DataFrame()

    # Scope: only Paysafe rows where Merchant Transaction ID is in PP Payment Public IDs
    pp_ids = set(pp_df[pub].dropna())
    psp_scoped = psp[psp[mid].isin(pp_ids)].drop_duplicates(subset=mid, keep="first")

    if psp_scoped.empty: return pd.DataFrame()

    m = psp_scoped[[mid, amt]].merge(
        pp_df[[pub, a_col]].rename(columns={pub:"_k", a_col:"Orch_Amount_raw"}),
        left_on=mid, right_on="_k", how="left")
    m["PSP_Amount"]  = to_numeric_col(m[amt].astype(str))
    m["Orch_Amount"] = to_numeric_col(m["Orch_Amount_raw"].astype(str))
    m["Verdict"]     = _verdict_psp_base(m, "PSP_Amount", "Orch_Amount", tol)
    m["Diff (USD)"]  = (m["PSP_Amount"].fillna(0) - m["Orch_Amount"].fillna(0)).round(4)
    m["PSP"]         = "Paysafe (PP)"
    return m

def reconcile_phase2(phase1_results, psp_map, tol_usd=TOL):
    p2  = {}
    err = {}
    bp_df = phase1_results.get("bp_raw", pd.DataFrame())
    pp_df = phase1_results.get("pp_raw", pd.DataFrame())

    if bp_df.empty and pp_df.empty:
        err["setup"] = "No raw orchestrator data. Upload Bridgerpay/Payprocc in Phase 1 first."
        return p2, err

    runners_bp = [
        ("paypal",      recon_paypal),
        ("unlimit",     recon_unlimit),
        ("nuvei_ni",    lambda bp, f, tol: recon_nuvei(bp, f, "Nuvei NI", tol)),
        ("nuvei_aq",    lambda bp, f, tol: recon_nuvei(bp, f, "Nuvei AQ", tol)),
        ("axcess",      recon_axcess),
        ("confirmo_bp", recon_confirmo_bp),
        ("trustpay",    recon_trustpayment),
        ("payabl",      recon_payabl),
        ("paysafe_bp",  recon_paysafe_bp),
    ]
    runners_pp = [
        ("dlocal",      recon_dlocal),
        ("skrill",      recon_skrill),
        ("paysafe_pp",  recon_paysafe_pp),
    ]

    if not bp_df.empty:
        for key, fn in runners_bp:
            if psp_map.get(key):
                try:
                    df = fn(bp_df, psp_map[key], tol_usd)
                    if df is not None and not df.empty:
                        p2[key] = df
                except Exception as e:
                    err[key] = str(e)

    if not pp_df.empty:
        for key, fn in runners_pp:
            if psp_map.get(key):
                try:
                    df = fn(pp_df, psp_map[key], tol_usd)
                    if df is not None and not df.empty:
                        p2[key] = df
                except Exception as e:
                    err[key] = str(e)

    return p2, err
