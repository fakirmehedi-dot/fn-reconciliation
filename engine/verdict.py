"""
engine/verdict.py  — Automated verdict / root-cause engine
Assigns a human-readable status to every reconciliation row.
"""
import pandas as pd, numpy as np

VERDICTS = {
    "FULLY_RECONCILED":      "Fully Reconciled",
    "AMOUNT_TOLERANCE":      "Amount Mismatch Within Tolerance",
    "AMOUNT_MISMATCH":       "Amount Mismatch",
    "MISSING_IN_PSP":        "Missing in PSP",
    "MISSING_IN_ORCH":       "Missing in Orchestrator",
    "EXTRA_IN_BANK":         "Extra in Bank (Not in API)",
    "CURRENCY_CONVERSION":   "Currency Conversion Issue",
    "TIMING_DELAY":          "Timing Delay",
    "DUPLICATE_TXN":         "Duplicate Transaction",
    "PARTIAL_RECON":         "Partial Reconciliation",
    "MANUAL_REVIEW":         "Manual Review Required",
}

def assign_verdict(row, api_amt_col="Grand Total", bank_amt_col="Bank_Amount",
                   verdict_col="Verdict", tol_usd=0.01, tol_crypto=2.00):
    """Assign a detailed verdict string to a single reconciliation row."""
    v = str(row.get(verdict_col, "")).upper()
    api_amt  = pd.to_numeric(row.get(api_amt_col, 0), errors="coerce") or 0
    bank_amt = pd.to_numeric(row.get(bank_amt_col, 0), errors="coerce") or 0
    diff     = abs(api_amt - bank_amt)
    bank     = str(row.get("Bank", row.get("PSP", ""))).lower()

    # Crypto PSPs get higher tolerance
    is_crypto = any(x in bank for x in ["coinsbuy","b2b","crypto","usdt"])
    tol = tol_crypto if is_crypto else tol_usd

    if "NOT IN BANK" in v or "NOT IN PSP" in v or "NOT IN ORCH" in v:
        return VERDICTS["MISSING_IN_PSP"]

    if "RECONCILED" in v:
        return VERDICTS["FULLY_RECONCILED"]

    if "AMOUNT MISMATCH" in v:
        if diff <= tol:
            return VERDICTS["AMOUNT_TOLERANCE"]
        if diff / max(api_amt, 0.01) > 0.10:
            return VERDICTS["MANUAL_REVIEW"]
        if any(x in bank for x in ["dlocal","skrill","paysafe"]) and diff < 50:
            return VERDICTS["CURRENCY_CONVERSION"]
        return VERDICTS["AMOUNT_MISMATCH"]

    return VERDICTS["MANUAL_REVIEW"]


def enrich_combined(combined_df, tol_usd=0.01, tol_crypto=2.00):
    """Add 'Auto Verdict' and 'Root Cause' columns to the combined Phase 1 DataFrame."""
    df = combined_df.copy()
    df["Auto Verdict"] = df.apply(
        lambda r: assign_verdict(r, tol_usd=tol_usd, tol_crypto=tol_crypto), axis=1)

    def root_cause(row):
        v = row.get("Auto Verdict", "")
        if v == VERDICTS["FULLY_RECONCILED"]:    return "—"
        if v == VERDICTS["MISSING_IN_PSP"]:      return "Transaction not settled in PSP statement"
        if v == VERDICTS["AMOUNT_TOLERANCE"]:    return "Minor rounding difference within tolerance"
        if v == VERDICTS["CURRENCY_CONVERSION"]: return "FX conversion rounding (expected)"
        if v == VERDICTS["AMOUNT_MISMATCH"]:
            amt = abs(pd.to_numeric(row.get("Diff (USD)", 0), errors="coerce") or 0)
            return f"Amount difference of ${amt:,.2f} — verify PSP statement"
        if v == VERDICTS["MANUAL_REVIEW"]:       return "Large discrepancy — manual review required"
        return "—"

    df["Root Cause"] = df.apply(root_cause, axis=1)
    return df
