# AI Intel Scraper MVP

RSS intelligence aggregation pipeline with rule-based fallback and optional LLM enhancement.

## Architecture

```
Z1 Ingestion        Z2 AI Core           Z3 Storage & Delivery    Z4 Deep Analysis     Z5 Education Renderer
┌──────────┐       ┌──────────┐          ┌──────────┐            ┌────────────────┐   ┌──────────────────┐
│ RSS Fetch │──────>│ Chain A  │─────────>│ SQLite   │───────────>│ 7-dim Deep Dive│──>│ 教育版報告        │
│ Clean     │      │ Chain B  │          │ digest.md│            │ Meta Signals   │   │ Notion/PPT 友善  │
│ Dedup     │      │ Chain C  │          │ Notion?  │            │ Macro Themes   │   │ 0 基礎 QA        │
│ Filter    │      │ Gates    │          │ Feishu?  │            │ Opportunity Map│   │ 圖文影片占位      │
│ Batch=20  │      │          │          │          │            │ Action Signals │   │                  │
└──────────┘       └──────────┘          └──────────┘            └────────────────┘   └──────────────────┘
```

## Quick Start (PowerShell)

```powershell
# 1. Navigate to project
cd C:\Projects\ai捕捉資訊\ai-intel-scraper-mvp

# 2. Create and activate virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create .env from example
Copy-Item .env.example .env

# 5. Initialize database
python scripts\init_db.py

# 6. Run pipeline once
python scripts\run_once.py

# 7. (Optional) Run scheduler loop (every 15 minutes)
python scripts\run_scheduler.py
```

## Developer Guide

```powershell
pip install -r requirements.txt
pip install -e .

lint
typecheck
test
check
```

品質標準：
- lint 會進行規則檢查與自動修正，並套用格式化
- typecheck 進行靜態型別檢查
- test 執行全部單元測試
- check 依序執行 lint、typecheck、test

## Configuration

Edit `.env` to customize:

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `.\data\intel.db` | SQLite database path |
| `OUTPUT_DIGEST_PATH` | `.\outputs\digest.md` | Digest output path |
| `LOG_PATH` | `.\logs\app.log` | Log file path |
| `RSS_FEEDS_JSON` | 3 feeds | JSON array of feed configs |
| `LLM_PROVIDER` | `none` | Set to `deepseek` or `openai` to enable LLM |
| `LLM_BASE_URL` | - | OpenAI-compatible API base URL |
| `LLM_API_KEY` | - | API key for LLM provider |
| `LLM_MODEL` | `deepseek-chat` | Model name |
| `GATE_MIN_SCORE` | `7.0` | Minimum score to pass quality gate |
| `GATE_MAX_DUP_RISK` | `0.25` | Maximum duplicate risk to pass |
| `NOTION_TOKEN` | - | Optional Notion integration token |
| `NOTION_DATABASE_ID` | - | Optional Notion database ID |
| `FEISHU_WEBHOOK_URL` | - | Optional Feishu webhook URL |
| `DEEP_ANALYSIS_ENABLED` | `true` | Enable Z4 deep analysis |
| `DEEP_ANALYSIS_OUTPUT_PATH` | `.\outputs\deep_analysis.md` | Deep analysis output path |
| `EDU_REPORT_ENABLED` | `true` | Enable Z5 education report |
| `EDU_REPORT_MAX_ITEMS` | `0` | Max items in education report (0=unlimited) |
| `EDU_REPORT_LANGUAGE` | `zh-TW` | Education report language |
| `EDU_REPORT_INCLUDE_MEDIA_PLACEHOLDERS` | `true` | Include image/video placeholders |

## Modes

### Zero-Key Mode (Default)

With `LLM_PROVIDER=none`, the pipeline uses rule-based heuristics:
- **Chain A**: Extracts entities via capitalization, generates summary from first 200 chars
- **Chain B**: Scores based on body length, entity count, source reputation
- **Chain C**: Generates Feishu card markdown from template

### LLM Mode

Set `LLM_PROVIDER=deepseek` (or any OpenAI-compatible provider) with `LLM_BASE_URL` and `LLM_API_KEY`. Each chain will attempt LLM first and fall back to rules on failure.

## Output

- **digest.md**: Markdown digest of items that passed quality gates
- **deep_analysis.md**: 5-part deep intelligence report (Z4) with per-item 7-dimension analysis
- **deep_analysis_education.md**: Education-friendly report (Z5) for beginners
- **Console**: Summary table with scores and tags
- **SQLite**: Full persistence of raw items and AI results
- **Notion** (optional): Database pages with scores and tags
- **Feishu** (optional): Interactive card messages via webhook

## Windows Git 建議設定

```powershell
# 確保 LF 不被自動轉換（本 repo 以 .gitattributes 控制 EOL）
git config --global core.autocrlf false

# 避免中文路徑 / 檔名顯示為 escape sequence
git config --global core.quotepath false

# 確保 commit / log 使用 UTF-8
git config --global i18n.commitEncoding utf-8
git config --global i18n.logOutputEncoding utf-8
```

**遇到 PowerShell 亂碼？** 在腳本開頭加入：
```powershell
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

**遇到 git diff 出現大量 CRLF 變更？** 執行：
```powershell
git add --renormalize .
git commit -m "chore: renormalize line endings"
```

## Generated Reports（Artifact Policy）

以下檔案是 pipeline 執行時自動產生的 **build artifacts**，不納入版控：

- `docs/reports/deep_analysis_education_version.md`
- `docs/reports/deep_analysis_education_version_ppt.md`
- `docs/reports/deep_analysis_education_version_xmind.md`
- `outputs/deep_analysis_education.md`（已被 `outputs/` 規則忽略）

這些檔案在 `.gitignore` 中被排除。如需分享報告，請直接傳送檔案或透過 CI release artifact。

`docs/reports/` 下的規格文件（如 `education_renderer_spec.md`）仍然保留在版控中。
測試用 golden snapshot（`tests/golden_*.md`）也保留在版控中。

## 如何啟用教育版報告（Z5）

1. 在 `.env` 中設定 `EDU_REPORT_ENABLED=true`（預設已開啟）
2. 執行 `python scripts\run_once.py`（或 `scripts\run.ps1`）
3. 報告產出位置：
   - `outputs/deep_analysis_education.md` — pipeline 輸出副本
   - `docs/reports/deep_analysis_education_version.md` — Notion 友善版
   - `docs/reports/deep_analysis_education_version_ppt.md` — PPT 切片版
4. 停用：設定 `EDU_REPORT_ENABLED=false`
5. 詳細規格：見 `docs/reports/education_renderer_spec.md`
