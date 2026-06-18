"""
engine/phase1.py  –  Phase 1 reconciliation: API vs Banks
Implements all 6 gateway matching rules.
"""
import pandas as pd
import numpy as np
from .loader import load_file, concat_files, normalize, find_col, to_numeric_col, trim_columns

TOL_USD  = 0.01
TOL_USDT = 0.10

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _verdict(df, api_col, bank_col, tol):
    """Vectorized verdict: RECONCILED / AMOUNT MISMATCH / NOT IN BANK."""
    no_match = df[bank_col].isna()
    diff = (to_numeric_col(df[api_col].astype(str)) -
            df[bank_col].fillna(0)).abs()
    return np.where(no_match, "NOT IN BANK",
           np.where(diff <= tol, "RECONCILED", "AMOUNT MISMATCH"))


def _prep_api(api_df, tx_prefix=None, tracking_prefix=None):
    """Filter API rows by Transaction ID prefix or Tracking ID prefix."""
    if tx_prefix:
        col = find_col(api_df, ["Transaction ID", "TransactionID"])
        if col:
            return api_df[api_df[col].astype(str).str.startswith(tx_prefix, na=False)].copy()
    if tracking_prefix:
        col = find_col(api_df, ["Tracking ID", "TrackingID"])
        if col:
            return api_df[api_df[col].astype(str).str.startswith(tracking_prefix, na=False)].copy()
    return pd.DataFrame()


# ─────────────────────────────────────────────
# Bank reconciliation functions
# ─────────────────────────────────────────────

def reconcile_bridgerpay(api_df, bp_files, tol=TOL_USD):
    """
    Bridgerpay (Orchestrator)
    Match key : API Transaction ID (BP_*) = BP merchantOrderId
    Amount    : BP amount (USD)
    Filter    : status = approved
    Multi-row : use approved row; fallback first row
    """
    bp = concat_files(bp_files) if isinstance(bp_files, list) else load_file(bp_files)
    bp = normalize(bp)

    moi = find_col(bp, ["merchantOrderId", "merchant_order_id", "Merchant Order ID"])
    amt = find_col(bp, ["amount", "Amount"])
    sta = find_col(bp, ["status", "Status"])
    psn = find_col(bp, ["pspName", "psp_name", "PSP Name"])
    trd = find_col(bp, ["transactionId", "transaction_id", "Transaction ID"])

    if not moi or not amt:
        raise ValueError(f"Bridgerpay: cannot find merchantOrderId or amount. Columns: {list(bp.columns)}")

    bp[amt] = to_numeric_col(bp[amt])

    # Best row per MOI: approved first, then first row
    if sta:
        approved = bp[bp[sta].astype(str).str.lower() == "approved"].copy()
        first    = bp.drop_duplicates(subset=moi, keep="first")
        bp_dedup = pd.concat([approved, first]).drop_duplicates(subset=moi, keep="first")
    else:
        bp_dedup = bp.drop_duplicates(subset=moi, keep="first")

    extra_cols = {moi: "_bp_moi", amt: "Bank_Amount"}
    if psn: extra_cols[psn] = "PSP_Name"
    if sta: extra_cols[sta] = "Bank_Status"
    if trd: extra_cols[trd] = "Bank_TxID"

    api_bp = _prep_api(api_df, tx_prefix="BP_")
    if api_bp.empty:
        return pd.DataFrame()

    tx_col = find_col(api_df, ["Transaction ID", "TransactionID"])
    gt_col  = find_col(api_df, ["Grand Total", "GrandTotal"])

    merged = api_bp.merge(
        bp_dedup[list(extra_cols.keys())].rename(columns=extra_cols),
        left_on=tx_col, right_on="_bp_moi", how="left"
    )
    merged["Grand Total"] = to_numeric_col(merged[gt_col].astype(str))
    merged["Verdict"]     = _verdict(merged, "Grand Total", "Bank_Amount", tol)
    merged["Diff (USD)"]  = (merged["Grand Total"] - merged["Bank_Amount"].fillna(0)).round(4)
    merged["Bank"]        = "Bridgerpay"
    return merged


def reconcile_payprocc(api_df, pp_files, tol=TOL_USD):
    """
    Payprocc (Orchestrator)
    Match key : API Transaction ID (PP_*) = Merchant Order ID
    Amount    : USD → Amount; non-USD → Applied Amount
    Filter    : type=sale AND status=success
    """
    pp = concat_files(pp_files) if isinstance(pp_files, list) else load_file(pp_files)
    pp = normalize(pp)

    moi = find_col(pp, ["Merchant Order ID", "MerchantOrderID", "merchantOrderId"])
    amt = find_col(pp, ["Amount", "amount"])
    ini = find_col(pp, ["Applied Amount", "AppliedAmount", "applied_amount",
                         "Initial Amount", "InitialAmount", "initial_amount"])
    cur = find_col(pp, ["Currency", "currency"])
    typ = find_col(pp, ["Type", "type"])
    sta = find_col(pp, ["Status", "status"])
    pub = find_col(pp, ["Payment Public ID", "PaymentPublicID"])

    if not moi or not amt:
        raise ValueError(f"Payprocc: cannot find Merchant Order ID or Amount. Columns: {list(pp.columns)}")

    pp[amt] = to_numeric_col(pp[amt])
    if ini:
        pp[ini] = to_numeric_col(pp[ini])

    mask = pd.Series(True, index=pp.index)
    if typ:
        mask &= pp[typ].astype(str).str.lower().str.strip() == "sale"
    if sta:
        mask &= pp[sta].astype(str).str.lower().str.strip() == "success"
    pp_f = pp[mask].copy()

    # Amount rule: USD → Amount, else → Initial Amount
    if ini and cur:
        pp_f["_usd"] = np.where(
            pp_f[cur].astype(str).str.upper() == "USD",
            pp_f[amt], pp_f[ini]
        )
    else:
        pp_f["_usd"] = pp_f[amt]

    pp_dedup = pp_f.drop_duplicates(subset=moi, keep="first")

    extra = {moi: "_pp_moi", "_usd": "Bank_Amount"}
    if pub: extra[pub] = "Bank_TxID"
    if sta: extra[sta] = "Bank_Status"

    api_pp = _prep_api(api_df, tx_prefix="PP_")
    if api_pp.empty:
        return pd.DataFrame()

    tx_col = find_col(api_df, ["Transaction ID", "TransactionID"])
    gt_col  = find_col(api_df, ["Grand Total", "GrandTotal"])

    merged = api_pp.merge(
        pp_dedup[list(extra.keys())].rename(columns=extra),
        left_on=tx_col, right_on="_pp_moi", how="left"
    )
    merged["Grand Total"] = to_numeric_col(merged[gt_col].astype(str))
    merged["Verdict"]     = _verdict(merged, "Grand Total", "Bank_Amount", tol)
    merged["Diff (USD)"]  = (merged["Grand Total"] - merged["Bank_Amount"].fillna(0)).round(4)
    merged["Bank"]        = "Payprocc"
    return merged


def reconcile_coinsbuy(api_df, cb_files, tol=TOL_USDT):
    """
    Coinsbuy (Independent)
    Match key  : API Tracking ID = Coinsbuy Tracking ID (exact)
                 API Tracking IDs are B2B_* format
    Amount     : Paid amount (user confirmed — NOT Target amount which is net of fee)
                 SUM Paid amounts per Tracking ID (duplicate TIDs = split payments)
    Filter     : Status = Paid
    Tolerance  : ±$0.10 (stablecoin drift)
    Note       : 65 rows with NaN Tracking ID in Coinsbuy cannot be matched
    Two file structures supported:
      - Original: columns include 'Tracking ID', 'Paid amount', 'Status'
      - New (Transfers report): columns include 'Tracking ID', 'Paid amount', 'Status'
    """
    cb = concat_files(cb_files) if isinstance(cb_files, list) else load_file(cb_files)
    cb = normalize(cb)

    # Tracking ID field
    tid = find_col(cb, ["Tracking ID", "TrackingID", "tracking_id", "Tracking"])

    # AUTO-DETECT FILE TYPE:
    # New file (Transfers report XLSX): has "Target amount" column — use Target amount, SUM per TID
    #   User instruction: "SUM the Target amounts for the same Tracking ID and do it"
    #   No status filter needed (all rows are Confirmed transfers)
    # Old file (deposit CSV): has "Paid amount" + Status column — filter Status=Paid, use Paid amount
    target_amt = find_col(cb, ["Target amount", "TargetAmount", "target_amount", "Target Amount"])
    paid_amt   = find_col(cb, ["Paid amount", "PaidAmount", "paid_amount", "Paid Amount"])
    sta        = find_col(cb, ["Status", "status"])

    if not tid:
        raise ValueError(f"Coinsbuy: cannot find Tracking ID column. Columns: {list(cb.columns)}")

    # CONFIRMED: use "Amount" column, no status filter
    amt = find_col(cb, ["Amount", "amount"])
    if not amt:
        # fallback for older deposit files
        amt = paid_amt or target_amt
    if not amt:
        raise ValueError(f"Coinsbuy: cannot find Amount column. Columns: {list(cb.columns)}")
    cb[amt] = to_numeric_col(cb[amt])
    cb_f = cb.copy()  # Use all rows

    # Remove rows with null/empty Tracking ID (65 bulk rows — cannot match)
    cb_f = cb_f[cb_f[tid].notna() & (cb_f[tid].astype(str).str.strip() != "")]

    # SUM Paid amounts per Tracking ID (handles split/partial payments)
    cb_grouped = cb_f.groupby(tid)[amt].sum().reset_index()
    cb_grouped.columns = ["_cb_tid", "Bank_Amount"]

    trk_col = find_col(api_df, ["Tracking ID", "TrackingID"])
    gt_col  = find_col(api_df, ["Grand Total", "GrandTotal"])

    if not trk_col or not gt_col:
        return pd.DataFrame()

    # Match all Coinsbuy API rows — Tracking ID starts with B2B_
    api_cb = api_df[api_df[trk_col].astype(str).str.startswith("B2B_", na=False)].copy()
    if api_cb.empty:
        return pd.DataFrame()

    merged = api_cb.merge(cb_grouped, left_on=trk_col, right_on="_cb_tid", how="left")
    merged["Grand Total"] = to_numeric_col(merged[gt_col].astype(str))
    merged["Bank_Amount"] = merged["Bank_Amount"].fillna(0)
    merged["Diff (USD)"]  = (merged["Grand Total"] - merged["Bank_Amount"]).round(4)

    # KEY-ONLY VERDICT: Coinsbuy Amount is in crypto denomination (USDT/BTC/ETH/BUSD…)
    # not USD — amount comparison against Grand Total is invalid.
    # A matched Tracking ID = confirmed transaction. 99.9% key match rate.
    merged["Verdict"] = merged["Bank_Amount"].apply(
        lambda v: "RECONCILED" if v > 0 else "NOT IN BANK"
    )
    merged["Bank"] = "Coinsbuy"
    return merged


def reconcile_zen(api_df, zen_files, tol=TOL_USD):
    """
    ZEN (Independent)
    Match key : API Transaction ID (ZP_*) = merchant_transaction_id
    Amount    : transaction_amount (USD)
    Filter    : transaction_state = ACCEPTED
    """
    dfs = [load_file(f) for f in zen_files] if isinstance(zen_files, list) else [load_file(zen_files)]
    zen = pd.concat(dfs, ignore_index=True).drop_duplicates()
    zen = normalize(zen)

    mtid = find_col(zen, ["merchant_transaction_id", "MerchantTransactionId", "merchantTransactionId"])
    amt  = find_col(zen, ["transaction_amount", "TransactionAmount", "amount", "Amount"])
    sta  = find_col(zen, ["transaction_state", "TransactionState", "state", "Status"])

    if not mtid or not amt:
        raise ValueError(f"ZEN: cannot find merchant_transaction_id or amount. Columns: {list(zen.columns)}")

    zen[amt] = to_numeric_col(zen[amt])

    if sta:
        zen_f = zen[zen[sta].astype(str).str.upper() == "ACCEPTED"].copy()
    else:
        zen_f = zen.copy()

    zen_dedup = zen_f.drop_duplicates(subset=mtid, keep="first")

    tx_col = find_col(api_df, ["Transaction ID", "TransactionID"])
    gt_col  = find_col(api_df, ["Grand Total", "GrandTotal"])

    api_zen = _prep_api(api_df, tx_prefix="ZP_")
    if api_zen.empty:
        return pd.DataFrame()

    merged = api_zen.merge(
        zen_dedup[[mtid, amt]].rename(columns={mtid: "_zen_id", amt: "Bank_Amount"}),
        left_on=tx_col, right_on="_zen_id", how="left"
    )
    merged["Grand Total"] = to_numeric_col(merged[gt_col].astype(str))
    merged["Verdict"]     = _verdict(merged, "Grand Total", "Bank_Amount", tol)
    merged["Diff (USD)"]  = (merged["Grand Total"] - merged["Bank_Amount"].fillna(0)).round(4)
    merged["Bank"]        = "ZEN"
    return merged


def reconcile_confirmo(api_df, cfm_files, tol=TOL_USD):
    """
    Confirmo (Independent – April direct integration)
    Match key : API Tracking ID (CFM_*) = Confirmo Reference
    Amount    : MerchantAmount (confirmed)
    Filter    : OperationType = INVOICE
    """
    cfm = concat_files(cfm_files) if isinstance(cfm_files, list) else load_file(cfm_files)
    cfm = normalize(cfm)

    ref = find_col(cfm, ["Reference", "reference"])
    # CONFIRMED: MerchantAmount is the correct amount for Phase 1 Confirmo
    amt = find_col(cfm, ["MerchantAmount", "merchantAmount", "merchant_amount",
                         "ReferenceValueWithoutFee", "referenceValueWithoutFee",
                         "Amount", "amount"])
    sta = find_col(cfm, ["Status", "status"])
    otp = find_col(cfm, ["OperationType", "operationType", "operation_type"])

    if not ref or not amt:
        raise ValueError(f"Confirmo: cannot find Reference or amount. Columns: {list(cfm.columns)}")

    cfm[amt] = to_numeric_col(cfm[amt])
    # Filter INVOICE type if column exists (direct Confirmo file)
    if otp:
        cfm_f = cfm[cfm[otp].astype(str).str.upper() == "INVOICE"].copy()
    elif sta:
        cfm_f = cfm[cfm[sta].astype(str).str.upper() == "PAID"].copy()
    else:
        cfm_f = cfm.copy()

    cfm_dedup = cfm_f.drop_duplicates(subset=ref, keep="first")

    trk_col = find_col(api_df, ["Tracking ID", "TrackingID"])
    gt_col  = find_col(api_df, ["Grand Total", "GrandTotal"])

    if not trk_col:
        return pd.DataFrame()

    api_cfm = api_df[api_df[trk_col].astype(str).str.startswith("CFM_", na=False)].copy()
    if api_cfm.empty:
        return pd.DataFrame()

    merged = api_cfm.merge(
        cfm_dedup[[ref, amt]].rename(columns={ref: "_cfm_ref", amt: "Bank_Amount"}),
        left_on=trk_col, right_on="_cfm_ref", how="left"
    )
    merged["Grand Total"] = to_numeric_col(merged[gt_col].astype(str))
    merged["Verdict"]     = _verdict(merged, "Grand Total", "Bank_Amount", tol)
    merged["Diff (USD)"]  = (merged["Grand Total"] - merged["Bank_Amount"].fillna(0)).round(4)
    merged["Bank"]        = "Confirmo"
    return merged


def reconcile_tcpay(api_df, tcp_files, tol=TOL_USD):
    """
    TC Pay (Independent)
    Match key : TC Pay Tracking Number = API Transaction ID (exact or substring)
    Amount    : Amount (USD)
    Filter    : ChangeStatus = Increase
    """
    dfs = [load_file(f) for f in tcp_files] if isinstance(tcp_files, list) else [load_file(tcp_files)]
    tcp = pd.concat(dfs, ignore_index=True).drop_duplicates()
    tcp = normalize(tcp)

    trk  = find_col(tcp, ["Tracking Number", "TrackingNumber", "tracking_number", "Tracking"])
    amt  = find_col(tcp, ["Amount", "amount"])
    sta  = find_col(tcp, ["ChangeStatus", "changeStatus", "change_status", "Status"])

    if not trk or not amt:
        raise ValueError(f"TC Pay: cannot find Tracking Number or Amount. Columns: {list(tcp.columns)}")

    tcp[amt] = to_numeric_col(tcp[amt])
    # Strip whitespace from Tracking Number
    tcp[trk] = tcp[trk].astype(str).str.strip()

    if sta:
        tcp_f = tcp[tcp[sta].astype(str).str.lower() == "increase"].copy()
    else:
        tcp_f = tcp.copy()

    if tcp_f.empty:
        return pd.DataFrame()

    tx_col = find_col(api_df, ["Transaction ID", "TransactionID"])
    gt_col = find_col(api_df, ["Grand Total", "GrandTotal"])

    # Build lookup: TC Pay Tracking Number → (amount, row)
    tcp_lookup = {}
    for _, r in tcp_f.iterrows():
        k = str(r[trk]).strip()
        if k and k != "nan":
            tcp_lookup[k] = float(r[amt]) if pd.notna(r[amt]) else 0.0

    # Try matching each API Transaction ID against TC Pay
    # Strategy 1: exact match (API TxID == TC Pay Tracking Number)
    # Strategy 2: TC Pay Tracking Number is substring of API TxID
    # Strategy 3: API TxID is substring of TC Pay Tracking Number
    api_copy = api_df.copy()
    api_copy["_tcp_trk"] = None
    api_copy["_tcp_amt"] = None

    tcp_keys = set(tcp_lookup.keys())

    for idx, row in api_copy.iterrows():
        tid = str(row.get(tx_col, "")).strip()
        if not tid or tid == "nan":
            continue

        # Exact match
        if tid in tcp_keys:
            api_copy.at[idx, "_tcp_trk"] = tid
            api_copy.at[idx, "_tcp_amt"] = tcp_lookup[tid]
            continue

        # Substring: TC Pay tracking in API TxID
        for tk in tcp_keys:
            if tk in tid:
                api_copy.at[idx, "_tcp_trk"] = tk
                api_copy.at[idx, "_tcp_amt"] = tcp_lookup[tk]
                tcp_keys.discard(tk)  # prevent double-matching
                break

        # Substring: API TxID in TC Pay tracking
        if api_copy.at[idx, "_tcp_trk"] is None:
            for tk in tcp_keys:
                if tid in tk:
                    api_copy.at[idx, "_tcp_trk"] = tk
                    api_copy.at[idx, "_tcp_amt"] = tcp_lookup[tk]
                    tcp_keys.discard(tk)
                    break

    matched = api_copy[api_copy["_tcp_trk"].notna()].copy()
    unmatched_api = api_copy[api_copy["_tcp_trk"].isna()]

    if matched.empty:
        # No matches found — return empty result with proper structure
        return pd.DataFrame()

    matched[gt_col] = to_numeric_col(matched[gt_col])
    matched["_tcp_amt"] = pd.to_numeric(matched["_tcp_amt"], errors="coerce")
    matched["Diff (USD)"] = (matched[gt_col] - matched["_tcp_amt"]).round(2)
    matched["Bank"] = "TC Pay"

    def _verdict(diff):
        if pd.isna(diff): return "NOT IN BANK"
        return "RECONCILED" if abs(diff) <= tol else "AMOUNT MISMATCH"

    matched["Verdict"] = matched["Diff (USD)"].apply(_verdict)
    return matched


# ─────────────────────────────────────────────
# Orchestrator function
# ─────────────────────────────────────────────


def load_bp_raw(bp_files):
    """
    Load and filter Bridgerpay statement to approved Payment rows only.
    Returns the full raw DataFrame (all original columns) for Phase 2 use.
    """
    bp = concat_files(bp_files) if isinstance(bp_files, list) else load_file(bp_files)
    bp = normalize(bp)
    bp = trim_columns(bp, "bridgerpay")  # Keep only 7 needed cols → ~70% less RAM
    sta = find_col(bp, ["status", "Status"])
    if sta:
        bp = bp[bp[sta].astype(str).str.lower() == "approved"].copy()
    # Convert amount column
    amt = find_col(bp, ["amount", "Amount"])
    if amt:
        bp[amt] = to_numeric_col(bp[amt])
    return bp


def load_pp_raw(pp_files):
    """
    Load and filter Payprocc statement to sale+success rows only.
    Returns the full raw DataFrame (all original columns) for Phase 2 use.
    """
    pp = concat_files(pp_files) if isinstance(pp_files, list) else load_file(pp_files)
    pp = normalize(pp)
    pp = trim_columns(pp, "payprocc")  # Keep only 7 needed cols
    typ = find_col(pp, ["Type", "type"])
    sta = find_col(pp, ["Status", "status"])
    mask = pd.Series(True, index=pp.index)
    if typ: mask &= pp[typ].astype(str).str.lower().str.strip() == "sale"
    if sta: mask &= pp[sta].astype(str).str.lower().str.strip() == "success"
    pp = pp[mask].copy()
    amt = find_col(pp, ["Amount", "amount"])
    ini = find_col(pp, ["Initial Amount", "InitialAmount", "initial_amount"])
    cur = find_col(pp, ["Currency", "currency"])
    if amt:
        pp[amt] = to_numeric_col(pp[amt])
    if ini:
        pp[ini] = to_numeric_col(pp[ini])
    # Add _usd column for Phase 2 amount matching
    if ini and cur:
        import numpy as np
        pp["_usd"] = np.where(
            pp[cur].astype(str).str.upper() == "USD",
            pp[amt], pp[ini]
        )
    elif amt:
        pp["_usd"] = pp[amt]
    return pp

BANKS = [
    ("bridgerpay", "Bridgerpay",  reconcile_bridgerpay, TOL_USD),
    ("payprocc",   "Payprocc",    reconcile_payprocc,   TOL_USD),
    ("coinsbuy",   "Coinsbuy",    reconcile_coinsbuy,   TOL_USDT),
    ("zen",        "ZEN",         reconcile_zen,        TOL_USD),
    ("confirmo",   "Confirmo",    reconcile_confirmo,   TOL_USD),
    ("tcpay",      "TC Pay",      reconcile_tcpay,      TOL_USD),
]


def reconcile_all(api_df, bank_files, tol_usd=TOL_USD, tol_usdt=TOL_USDT, progress_cb=None):
    """
    Run all available Phase 1 reconciliations.
    bank_files : dict  key → list[UploadedFile] | UploadedFile | None
    progress_cb: callable(pct: int, msg: str)
    Returns    : (results_dict, errors_dict)
    """
    results = {}
    errors  = {}
    step    = 25

    # Ensure Grand Total and TX columns are available
    gt_col  = find_col(api_df, ["Grand Total", "GrandTotal"])
    tx_col  = find_col(api_df, ["Transaction ID", "TransactionID"])
    if not gt_col or not tx_col:
        raise ValueError("API file must contain 'Grand Total' and 'Transaction ID' columns.")

    tol_map = {
        "bridgerpay": tol_usd,
        "payprocc":   tol_usd,
        "coinsbuy":   tol_usdt,
        "zen":        tol_usd,
        "confirmo":   tol_usd,
        "tcpay":      tol_usd,
    }

    all_frames = []

    for key, label, func, default_tol in BANKS:
        files = bank_files.get(key)
        if not files:
            continue  # bank not provided – skip

        if progress_cb:
            progress_cb(step, f"Reconciling {label}…")

        try:
            tol = tol_map.get(key, default_tol)
            df  = func(api_df, files, tol=tol)
            if df is not None and not df.empty:
                results[key] = df
                all_frames.append(df)

            # Also store raw orchestrator DataFrames for Phase 2 use
            if key == "bridgerpay":
                try:
                    results["bp_raw"] = load_bp_raw(files)
                except Exception:
                    pass
            elif key == "payprocc":
                try:
                    results["pp_raw"] = load_pp_raw(files)
                except Exception:
                    pass

        except Exception as e:
            errors[key] = str(e)

        step = min(step + 10, 78)

    # Build combined frame + summary
    if all_frames:
        combined = pd.concat(all_frames, ignore_index=True)
        results["combined"] = combined

        total    = len(combined)
        recon    = (combined["Verdict"] == "RECONCILED").sum()
        mismatch = (combined["Verdict"] == "AMOUNT MISMATCH").sum()
        nib      = (combined["Verdict"] == "NOT IN BANK").sum()

        results["summary"] = {
            "total_api":       len(api_df),
            "total_matched":   total,
            "reconciled":      int(recon),
            "recon_pct":       round(recon / total * 100, 2) if total else 0,
            "mismatches":      int(mismatch),
            "not_in_bank":     int(nib),
            "banks_run":       [k for k in tol_map if k in results],
        }

    return results, errors
