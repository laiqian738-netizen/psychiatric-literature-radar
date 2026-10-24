# Psychiatric Literature Radar

即時查詢精神疾病 × omics / rare variants / biobank / trio study / machine learning 文獻的 Streamlit 網站。**不含任何模擬論文。**

## 本機啟動

需要 Python 3.10+。

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
streamlit run app.py
```

瀏覽器開啟終端機顯示的 `http://localhost:8501`。

## 部署

1. 將 `app.py`、`requirements.txt`、`README.md` 上傳到自己的 GitHub repository。
2. 在 [Streamlit Community Cloud](https://share.streamlit.io/) 選擇 **Create app**，指定 repository、branch 及入口 `app.py`。
3. 部署後會得到公開網址。公開部署的 SQLite 收藏可能因容器重啟而遺失，且多位訪客會共用資料庫；正式多人使用建議接 Supabase / PostgreSQL 與登入功能。
4. 如遇 API 配額限制，選擇性設定環境變數 `NCBI_EMAIL`、`NCBI_API_KEY`、`OPENALEX_API_KEY`。不用金鑰仍可嘗試搜尋，但 OpenAlex 方案與限額可能變動。

## 搜尋邏輯與限制

- **新增類別**：Outcome 包含 Treatment-resistant depression (TRD)；Method 包含 Biobank study 與 Trio study / Family-based study。Biobank 和 trio study 是研究設計／資料來源類別，也納入 Method 篩選以符合介面需求。
- **AND**：一篇文章需在標題或摘要至少符合一個選定 outcome 及一個 method；各組內為 OR。採關鍵詞分類，可能有漏收或誤收。若沒有摘要，只檢查標題。
- 資料來源：NCBI E-utilities（PubMed）、Europe PMC、OpenAlex、arXiv Atom API、bioRxiv/medRxiv 官方 API。
- PubMed、Europe PMC 由 Boolean 查詢；OpenAlex 用多組詞彙做搜尋；arXiv 用組合查詢；bioRxiv/medRxiv 官方 API **沒有通用全文關鍵詞搜尋**，因此只從符合日期範圍內最近數頁做本地關鍵詞篩選，不是全面檢索。
- 每個 API 僅取有限筆候選文章，故結果不是 systematic review 等級的全面搜尋；不應把回傳篇數當作所有符合條件文獻的總數。
- 去重：DOI → PMID → 正規化標題；可能仍有預印本／正式出版版本未合併。
- 「更新」按鈕重新呼叫各 API；不是後台自動推播。
- SQLite 收藏儲存於執行網站所在的伺服器，不會同步不同部署環境。

## APIs / docs

- https://www.ncbi.nlm.nih.gov/books/NBK25501/
- https://europepmc.org/RestfulWebService
- https://docs.openalex.org/
- https://info.arxiv.org/help/api/user-manual.html
- https://api.biorxiv.org/
