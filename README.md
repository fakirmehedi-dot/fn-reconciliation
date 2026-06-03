# FundedNext Revenue Reconciliation Portal

## Deploy to Streamlit Cloud (5 minutes)

### Step 1: Create GitHub Repository
1. Go to [github.com/new](https://github.com/new)
2. Name it `fn-reconciliation` (set to **Private**)
3. Click "Create repository"

### Step 2: Upload Files
Click "uploading an existing file" on the empty repo page, then drag all these files:
```
app.py
requirements.txt
.streamlit/config.toml
engine/__init__.py
engine/loader.py
engine/phase1.py
engine/phase2.py
engine/writer.py
engine/verdict.py
engine/report_phase_summary.py
engine/report_summary.py
engine/report_order_wise.py
engine/report_mismatch.py
```
Click "Commit changes"

### Step 3: Deploy on Streamlit Cloud
1. Go to [share.streamlit.io](https://share.streamlit.io)
2. Click "New app"
3. Connect your GitHub account
4. Select your `fn-reconciliation` repo
5. Main file: `app.py`
6. Click "Deploy!"

### Step 4: Share
Your portal will be live at: `https://fn-reconciliation-xxxxx.streamlit.app`

Share this URL with anyone — they can upload files and see results directly in their browser.

### Notes
- **Max file upload**: 500 MB (configured in `.streamlit/config.toml`)
- **Private repo**: Only people with the URL can access the portal
- **Free tier**: Streamlit Cloud free plan supports 1 private app
- **Auto-updates**: Push changes to GitHub → portal auto-redeploys
