#!/bin/bash
cd "$(dirname "$0")"
echo ""
echo "  FundedNext Revenue Reconciliation"
echo "  ================================="
echo ""
echo "  Starting portal... (browser will open automatically)"
echo "  Press Ctrl+C to stop, then close this window."
echo ""
streamlit run app.py --server.maxUploadSize 500
