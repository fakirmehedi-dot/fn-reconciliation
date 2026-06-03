"""
engine/loader.py  –  Smart file loader
Handles CSV / XLSX / XLS with various encodings and separators.
"""
import pandas as pd
import io


def load_file(uploaded_file, skip_rows=0):
    uploaded_file.seek(0)
    content = uploaded_file.read()
    name = getattr(uploaded_file, 'name', '').lower()

    if name.endswith(".csv") or name == '':
        return _load_csv(content, skip_rows=skip_rows)
    elif name.endswith((".xlsx", ".xlsm")):
        return pd.read_excel(io.BytesIO(content), engine="openpyxl",
                              skiprows=skip_rows, dtype=str)
    elif name.endswith(".xls"):
        return pd.read_excel(io.BytesIO(content), engine="xlrd",
                              skiprows=skip_rows, dtype=str)
    else:
        try:   return _load_csv(content, skip_rows=skip_rows)
        except Exception:
               return pd.read_excel(io.BytesIO(content), skiprows=skip_rows, dtype=str)


def _load_csv(content, skip_rows=0):
    """Try various encodings / separators to load a CSV.
    Handles: UTF-8 BOM, sep= declaration row (European Excel CSV).
    """
    # Detect sep= declaration row (e.g. sep=, or "sep=," or sep=;)
    # Handles: UTF-8 BOM, quoted form "sep=,", bare form sep=,
    try:
        header = content[:80].decode("utf-8-sig", errors="ignore")
        header = header.lstrip("\ufeff\ufffe\ufffd")
        first_line = header.splitlines()[0].strip()
        # Strip surrounding quotes then check
        normalized = first_line.strip('"\'').lower()
        if normalized.startswith("sep="):
            skip_rows = max(skip_rows, 1)
    except Exception:
        pass

    for enc in ["utf-8-sig", "utf-8", "latin-1", "cp1252"]:
        for sep in [",", ";", "\t", "|"]:
            try:
                df = pd.read_csv(io.BytesIO(content), sep=sep, encoding=enc,
                                  low_memory=False, skiprows=skip_rows, dtype=str)
                if len(df.columns) > 1:
                    return df
            except Exception:
                continue
    # Last resort
    return pd.read_csv(io.BytesIO(content), encoding="latin-1",
                        low_memory=False, skiprows=skip_rows, dtype=str)


def concat_files(file_list):
    dfs = [load_file(f) for f in file_list]
    combined = pd.concat(dfs, ignore_index=True)
    return combined.drop_duplicates()


def normalize(df):
    df.columns = [str(c).strip() for c in df.columns]
    return df


def find_col(df, candidates):
    mapping = {c.lower().replace(" ","").replace("_",""): c for c in df.columns}
    for cand in candidates:
        key = cand.lower().replace(" ","").replace("_","")
        if key in mapping:
            return mapping[key]
    return None


def to_numeric_col(series):
    return pd.to_numeric(
        series.astype(str)
              .str.replace(",", ".", regex=False)
              .str.replace(r"[^\d.\-]", "", regex=True),
        errors="coerce",
    )


# ── Memory-optimized column sets ─────────────────────────────────────────────
KEEP_COLS = {
    "api": ["Transaction ID","Tracking ID","Grand Total","Status","Created At",
            "Plan Type","Plan Name","Account Type","Order ID","Customer Email",
            "Gateway","Order Type","Updated At"],
    "bridgerpay": ["status","merchantOrderId","transactionId","pspOrderId",
                   "pspName","amount","id"],
    "payprocc": ["Type","Status","Merchant Order ID","Payment Public ID",
                 "Amount","Initial Amount","Currency"],
}


def trim_columns(df, file_type=None):
    """Keep only needed columns to save memory. Reduces RAM by ~70%."""
    if file_type and file_type in KEEP_COLS:
        needed = KEEP_COLS[file_type]
        keep = []
        for col in df.columns:
            for need in needed:
                if col.lower().strip() == need.lower().strip():
                    keep.append(col)
                    break
        if keep:
            return df[keep].copy()
    return df
