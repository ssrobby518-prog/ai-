# MVP Demo Script — AI Intel Scraper
_Iteration 8 · Generated 2026-02-23_

---

## Pre-Demo Checklist

| Item | Check |
|------|-------|
| Python env active (`venv` or conda) | `python --version` |
| `outputs/` directory exists | `ls outputs/` |
| (Optional) llama-server running for faithful ZH | `scripts\llama_server.ps1` |
| Git branch: `main` | `git status -sb` |

---

## DoD Evidence C — Changed Files (Iteration 8, commits 1bd9ccf..5f56680)

```
scripts/run_once.py             — Z0 hydration, latest_digest.md, desktop_button.meta.json
scripts/run_pipeline.ps1        — rewritten: uses run_once.py + PIPELINE_RUN_ID env
scripts/open_latest.ps1         — NEW: thin wrapper → open_ppt.ps1
scripts/install_daily_9am_task.ps1 — rewritten: Beijing TZ + scheduler.meta.json
scripts/uninstall_daily_task.ps1   — NEW
scripts/verify_online.ps1       — added DESKTOP_BUTTON + SCHEDULER gates
scripts/verify_run.ps1          — URL-strip before banned-word check
utils/fulltext_hydrator.py      — fixed TimeoutError handler (fut/u variable swap)
```

---

## DoD Evidence D — Demo Walk-Through

### Step 1 · Run the Pipeline (Desktop Button)

**Double-click** the desktop shortcut **OR** run in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_pipeline.ps1
```

Expected console output (last few lines):
```
=== AI Intel Scraper — run_id=YYYYMMDD_HHMMSS ===
...
desktop_button.meta.json written: exit_code=0
```

**What was generated:**

| File | Content |
|------|---------|
| `outputs/desktop_button.meta.json` | `run_id`, `success=true`, timestamps |
| `outputs/latest_digest.md` | Top-2 events with ZH Q1/Q2/Proof |
| `outputs/executive_brief_*.pptx` | Full slide deck |
| `outputs/executive_report_*.docx` | Full DOCX report |
| `outputs/fulltext_hydrator.meta.json` | Hydration stats (`ok=143`) |
| `outputs/newsroom_zh.meta.json` | ZH ratio pass |
| `outputs/canonical_v3.meta.json` | Canonical narrative pass |
| `outputs/faithful_zh_news.meta.json` | Faithful ZH pass |

---

### Step 2 · View the Digest

```powershell
cat outputs\latest_digest.md
```

Or open in any text editor. Shows **top-2 events** ranked by fulltext availability
and density score, with ZH Q1/Q2/Proof and verbatim `「...」` anchor quotes.

---

### Step 3 · View the Slide Deck

The run_pipeline.ps1 script automatically opens the latest PPT/DOCX on success
via `scripts\open_latest.ps1` → `scripts\open_ppt.ps1`.

If it did not open automatically:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\open_latest.ps1
```

---

### Step 4 · Install the Daily 09:00 Scheduler (P1)

Run **as Administrator** (right-click → "Run as administrator"):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_daily_9am_task.ps1
```

Expected output:
```
Task created: AIIntelScraper_Daily_0900_Beijing
Trigger: HH:MM local (= 09:00 Beijing)
scheduler.meta.json written
```

Verify in Task Scheduler UI: `taskschd.msc` → Task Scheduler Library →
find `AIIntelScraper_Daily_0900_Beijing`.

To uninstall:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\uninstall_daily_task.ps1
```

---

### Step 5 · Run Acceptance Verification

**Run ONCE after all changes are committed:**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify_online.ps1
```

Expected final output:
```
FULLTEXT_HYDRATION: PASS (coverage=0.067  ok=143)
DESKTOP_BUTTON:     PASS (run_id=YYYYMMDD_HHMMSS  exit_code=0)
SCHEDULER:          PASS  -- OR -- WARN-OK (if scheduler not installed)

=== verify_online.ps1 COMPLETE: all gates passed ===
```

Gate summary:

| Gate | Status | Notes |
|------|--------|-------|
| EXEC KPI GATES | PASS | sparse-day OK |
| EXEC TEXT BAN SCAN | PASS | 0 hits |
| NEWSROOM_ZH | PASS | avg>=0.35, min>=0.20 |
| NEWS_ANCHOR_GATE | PASS | coverage=1.000 |
| FAITHFUL_ZH_NEWS | PASS | applied>=2, coverage>=0.90, ellipsis=0 |
| FULLTEXT_HYDRATION | PASS | ok=143, coverage=0.067 |
| DESKTOP_BUTTON | PASS | run_id non-empty, success=true |
| SCHEDULER | WARN-OK | needs admin install to become PASS |

---

## Key Architecture Notes

- **Z0 mode**: Pipeline loads cached Z0 snapshot (`data/raw/z0/`) + runs fulltext
  hydration batch (60 s timeout, 4 workers).  Hydration writes
  `fulltext_hydrator.meta.json` which the FULLTEXT_HYDRATION gate reads.
- **Beijing TZ**: Scheduler converts 09:00 CST → local time via
  `.NET TimeZoneInfo` — works on any Windows locale.
- **Canonical narrative**: All Q1/Q2/Proof text flows from
  `utils/canonical_narrative.get_canonical_payload()` — single source of truth
  for PPT, DOCX, and `latest_digest.md`.
- **Faithful ZH**: When source fulltext is EN and >= 1200 chars, llama.cpp
  (Qwen 2.5-7B) generates extractive ZH sentences with verbatim EN anchors
  in `「...」` brackets.

---

_End of Demo Script_
