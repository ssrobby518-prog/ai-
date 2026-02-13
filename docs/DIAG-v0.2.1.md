# Pipeline Diagnosis Report — Post v0.2.1 Run

**專案**：ai-intel-scraper-mvp
**版本**：v0.2.1
**執行日期**：2026-02-12
**撰寫角色**：Tech Lead / AI Pipeline Architect

---

## 1. Executive Summary

> v0.2.1 的 full-text enrichment 修復已**成功解決資料來源問題**（85% HN 項目取得真實文章內容），但最終報告品質**未顯著提升**。根因不在資料層，而在 `core/deep_analyzer.py` 的 fallback 分析路徑——該模板將所有實體無差別套入「科技公司/產品」框架，完全不依據文章內容進行語義推理。本次執行證實：**資料品質是必要條件，但非充分條件；分析邏輯才是報告品質的瓶頸。**

---

## 2. Run Metrics

| 指標 | 數值 |
|------|------|
| 處理項目總數 | 31 |
| 通過門檻（passed gate） | 26 |
| 管線總耗時 | 46.08s |
| HN 項目 enrichment 成功數 | 17 / 20 |
| HN enrichment 成功率 | ≈ 85% |
| Enrichment 失敗項目 | 2（GPT-5.3、immigration） |
| 最終報告項目數 | 26 |

### Enrichment 時間開銷估算

| 項目 | 計算 |
|------|------|
| Enrichment 對象 | 20 筆 HN 項目 |
| Politeness delay | 20 × 0.5s = 10s |
| HTTP 抓取 + 抽取 | 依站點而異 |
| 佔管線總耗時比例 | 約 10s+ / 46.08s ≈ 22%+ |

---

## 3. Enrichment Layer Evaluation

### 成功案例

| 項目 | Enrichment 結果 | 核心事實品質 |
|------|-----------------|-------------|
| Taklamakan Desert 文章 | 成功抓取真實文章內容 | 核心事實包含完整句子：`"China has planted so many trees around the Taklamakan Desert..."` |
| El Paso balloon 文章 | 成功抓取 | 核心事實包含事件細節：`"A party balloon mistaken for a cartel drone shut down El Paso..."` |
| Omnara (YC S25) | 成功抓取 | 核心事實包含產品描述：`"We're building a web and mobile agentic IDE..."` |

### 失敗案例

| 項目 | 失敗表現 | 推測原因 |
|------|---------|---------|
| GPT-5.3 Codex Spark | body 仍為 `"Article URL: https://openai..."` | 來源站點封鎖 / trafilatura 抽取失敗 |
| Immigration (NBC) | body 仍為 `"Article URL: https://www..."` | 同上 |

### 判定

> Enrichment 層本身**運作正常**。85% 成功率在無重試機制、無 proxy 的前提下屬合理水準。失敗案例為預期中的長尾問題，不影響整體判斷。

---

## 4. Entity Extraction Failure Analysis

### 問題現象

Enrichment 成功的項目，其實體抽取仍出現嚴重語義錯誤：

| 原始文字中的詞彙 | 被抽取為實體 | 被下游分類為 | 正確語義 |
|-----------------|-------------|-------------|---------|
| Taklamakan | 是 | 能源企業 | 地理名稱（沙漠） |
| Desert | 是 | 監管機構 | 地理名稱（沙漠的一部分） |
| FAA | 是 | 企業 | 美國聯邦航空管理局（政府機構） |
| Paso | 是 | 技術開發者 | 城市名（El Paso） |
| Sign | 是 | 消費者 | 網頁中「Sign up」的殘留 |

### 根因

`core/entity_extraction.py` 的抽取邏輯基於 **title-case 序列偵測 + TF-IDF-like 評分**，缺乏：

- 實體類型分類（NER：人名 / 地名 / 機構名 / 產品名）
- 常見地名/機構名知識庫
- 上下文語義消歧

### 影響

即使 body 內容正確，錯誤的實體清單會直接傳入 deep_analyzer，導致後續所有分析基於錯誤前提展開。

---

## 5. Deep Analyzer Root Cause

### 核心問題

`core/deep_analyzer.py` 的 **fallback 分析路徑**（非 LLM 路徑）使用固定模板，將實體無差別填入預設框架：

```
模板邏輯（虛擬碼）：

for entity in entities:
    stakeholder_1 = f"技術開發者（如 {entities[0]}）"
    stakeholder_2 = f"平台營運方（如 {entities[1]}）"
    stakeholder_3 = f"終端使用者（如 {entities[2]}）"
    mechanism    = 從固定清單中選擇（採用曲線 / 可擴展性 / ...）
    analysis     = 模板.format(stakeholder_1, stakeholder_2, mechanism)
```

### 實際產出（Taklamakan Desert 文章）

| 欄位 | 產出內容 | 問題 |
|------|---------|------|
| 力場分析 | `能源企業（如 Taklamakan）：在轉型壓力下尋求新利潤來源` | Taklamakan 不是企業 |
| 力場分析 | `政府／監管（如 Desert）：推動減碳目標` | Desert 不是機構 |
| 力場分析 | `消費者（如 Sign）：期望更低成本` | Sign 是網頁殘留文字 |
| 第一性原理 | `可擴展性（scalability）` | 此文章主題為生態工程，非軟體擴展 |
| 二階效應 | `基於「China has planted...」，Taklamakan、Desert 的現有用戶需要評估相容性` | 沙漠沒有「用戶」 |
| 機會識別 | `針對能源企業（如 Taklamakan）的可擴展性需求` | 完全脫離文章語境 |

### 根因判定

> **Deep analyzer fallback 不進行內容理解**。它是純粹的「實體名 → 模板槽位」填充器。無論 body 內容多豐富，只要經過這條路徑，產出就是同質化的樣板文字。

---

## 6. Layer-by-Layer Pipeline Health Table

| 層級 | 模組 | 狀態 | 說明 |
|------|------|------|------|
| RSS / News Sources | `core/ingestion.py`, `core/news_sources.py` | 正常 | 70 items fetched，來源穩定 |
| Full-text Enrichment | `utils/article_fetch.py` | 部分成功 | 17/20 HN items enriched（85%），2 筆抓取失敗 |
| Dedup + Filter | `core/ingestion.py` | 正常 | 70 → 31 items，過濾邏輯運作正常 |
| Entity Extraction | `core/entity_extraction.py` | 有問題 | 地名/機構名被誤判為企業，缺乏 NER 能力 |
| AI Core (Schema A/B) | `core/ai_core.py` | 正常 | 評分與分類運作正常 |
| **Deep Analyzer** | **`core/deep_analyzer.py`** | **根本問題** | **fallback 模板不依內容推理，無差別套用科技公司框架** |
| Delivery | `core/deep_delivery.py` | 正常 | Markdown 渲染正常 |

---

## 7. Root Cause Architecture Diagram

```
┌─────────────────────────┐
│   RSS / News Sources    │
│   (hnrss, Algolia,      │
│    TechCrunch RSS)      │
└───────────┬─────────────┘
            ↓
┌─────────────────────────┐
│      Ingestion          │  ← 正常
└───────────┬─────────────┘
            ↓
┌─────────────────────────┐
│  Full-text Enrichment   │  ← 部分成功（85%）
│  (NEW v0.2.1)           │    2 筆抓取失敗
└───────────┬─────────────┘
            ↓
┌─────────────────────────┐
│  Entity Extraction      │  ← ⚠️ 有問題
│                         │    地名/機構名誤判為企業
└───────────┬─────────────┘
            ↓
┌═════════════════════════┐
║  Deep Analyzer          ║  ← ⚠️⚠️⚠️ ROOT CAUSE
║  (fallback path)        ║
║                         ║  實體 → 固定模板槽位
║  不讀 body 內容          ║  不做語義推理
║  不區分實體類型          ║  所有文章同質化產出
╚═══════════╤═════════════╝
            ↓
┌─────────────────────────┐
│     Final Report        │  ← 品質未提升
│   (deep_analysis.md)    │    模板化、脫離語境
└─────────────────────────┘
```

**問題傳播路徑**：

```
正確的 body 內容
      ↓
  錯誤的實體清單（entity_extraction 缺陷）
      ↓
  實體名填入固定模板（deep_analyzer fallback 缺陷）
      ↓
  同質化、脫離語境的分析報告
```

---

## 8. Why Enrichment Didn't Improve Final Reports

### 資料流斷點分析

| 步驟 | 輸入 | 輸出 | 品質 |
|------|------|------|------|
| Enrichment | metadata-only body | 真實文章全文 | 改善 |
| Entity Extraction | 真實文章全文 | 實體清單 | **未改善**：抽取邏輯不理解語義 |
| Deep Analyzer | 實體清單 + body | 分析報告 | **未改善**：fallback 不讀 body |

### 核心矛盾

> Enrichment 成功地將**正確資料**送入管線，但下游兩個模組**都不具備利用這些資料的能力**：
>
> 1. **Entity Extraction**：基於表面模式匹配，無法從豐富內容中抽取正確實體
> 2. **Deep Analyzer fallback**：完全不讀 body 內容，僅消費實體清單並填入模板
>
> 這意味著：即使 enrichment 成功率達到 100%，在現有架構下，最終報告品質仍不會改善。

---

## 9. Required Refactor: deep_analyzer Fallback

### 目標

將 fallback 路徑從「實體 → 模板填充」改為「內容 → 語義分析」。

### 改動範圍

| 檔案 | 改動類型 | 說明 |
|------|---------|------|
| `core/deep_analyzer.py` | 重構 | fallback 路徑需基於 body 內容生成分析 |
| `core/entity_extraction.py` | 增強 | 加入實體類型分類（至少區分：人名/地名/機構/產品） |
| `tests/test_deep_analysis.py` | 更新 | 新增非科技類文章的品質回歸測試 |

### Fallback 改進方向

```
【現行】
entities[] → 模板槽位填充 → 固定框架輸出

【目標】
body text → 內容摘要抽取 → 主題/領域偵測 → 對應領域模板 → 語境化輸出
              ↑                ↑
         直接使用 body     不只依賴 entities
```

### 最低可行改動

1. **Deep analyzer fallback 必須讀取 body 內容**：至少用 body 前 500 字作為分析依據
2. **實體角色不能硬編碼**：「技術開發者 / 平台營運方 / 終端使用者」三角色框架不適用於所有主題
3. **機制選擇需與內容相關**：不能從固定清單隨機選擇

---

## 10. Engineering Roadmap（Priority List）

| 優先級 | 任務 | 對應問題 | 預期效果 |
|--------|------|---------|---------|
| **P0** | 重構 `deep_analyzer.py` fallback：讀取 body 內容，基於文章語義生成分析 | Deep analyzer 不讀 body | 報告從模板化轉為內容驅動 |
| **P0** | 增強 `entity_extraction.py`：加入實體類型標註（人名/地名/機構/產品） | 地名被當企業 | 下游分析基於正確實體角色 |
| **P1** | 替換 fallback 的固定三角色框架為動態角色偵測（基於 body 內容 + 實體類型） | 所有文章套用科技公司框架 | 非科技文章獲得合理的力場分析 |
| **P1** | 機制選擇改為基於內容關鍵字匹配而非固定清單隨機選取 | 沙漠文章被分配「可擴展性」機制 | 機制與文章主題一致 |
| **P2** | Enrichment 失敗重試（1-2 次指數退避） | 2 筆 HN 項目抓取失敗 | 成功率從 85% 提升至 90%+ |
| **P2** | 新增非科技類文章的 golden snapshot 測試（氣候、政治、安全類） | 測試只覆蓋科技類 | 防止非科技文章的品質回歸 |
| **P3** | Entity extraction 加入常見地名/機構名知識庫 | Taklamakan、FAA 被誤判 | 減少明顯的實體分類錯誤 |
| **P3** | Enrichment 並行抓取（ThreadPoolExecutor） | 20 筆 × 0.5s = 10s+ 延遲 | 管線總耗時降低 |

---

## 11. Appendix — Evidence Logs

### A. 管線執行日誌（關鍵行）

```
2026-02-12T12:00:06 | Fetched feed HackerNews      | 20 entries | 6.24s
2026-02-12T12:00:50 | Enriched 17/70 items with full article text
2026-02-12T12:00:50 | Fetched 70 total raw items
2026-02-12T12:00:50 | Filters: 70 -> 31 items
2026-02-12T12:00:50 | PIPELINE COMPLETE | 31 processed | 26 passed | 46.08s total
```

### B. Enrichment 成功案例（核心事實對比）

| 項目 | Enriched body 摘要 | 核心事實品質 |
|------|-------------------|-------------|
| El Paso balloon | `"A party balloon mistaken for a cartel drone shut down El Paso for hours"` | 包含事件細節 |
| Taklamakan Desert | `"China has planted so many trees around the Taklamakan Desert..."` | 包含完整論述 |
| Omnara (YC S25) | `"We're building a web and mobile agentic IDE..."` | 包含產品描述 |

### C. Enrichment 失敗案例（body 仍為 metadata）

```
# GPT-5.3（項目 #2）
核心事實：
- Article URL: https://openai
- com/index/introducing-gpt-5-3-codex-spark/ Comments URL: https://news
- id=46992553 Points: 218 # Comments: 95

# Immigration（項目 #13）
核心事實：
- Article URL: https://www
- com/politics/immigration/trump-administration-working-expand-effort-strip-citizenship...
- id=46989630 Points: 50 # Comments: 55
```

### D. Deep Analyzer 樣板化輸出（Taklamakan Desert 文章）

```
力場分析：
  - 能源企業（如 Taklamakan）：在轉型壓力下尋求新利潤來源
  - 政府／監管（如 Desert）：推動減碳目標與能源安全
  - 消費者（如 Sign）：期望更低成本與更永續的能源選擇

第一性原理：
  選定機制：可擴展性（scalability）

二階效應：
  基於「China has planted...」，Taklamakan、Desert 的現有用戶需要評估相容性影響
```

> 上述產出中，`Taklamakan` 是沙漠名、`Desert` 是地理詞、`Sign` 是網頁殘留文字。所有角色分配與機制選擇均與文章內容無關。

### E. 問題傳播鏈

```
[資料層]  body = "China has planted so many trees around the Taklamakan Desert..."  ✅ 正確

     ↓ entity_extraction.py

[實體層]  entities = ["Taklamakan", "Desert", "Sign"]  ❌ 語義錯誤

     ↓ deep_analyzer.py (fallback)

[分析層]  stakeholder_1 = "能源企業（如 Taklamakan）"  ❌ 框架錯誤
          mechanism     = "可擴展性（scalability）"    ❌ 主題無關
          analysis      = 模板填充輸出                  ❌ 同質化

     ↓ deep_delivery.py

[報告層]  deep_analysis.md                              ❌ 品質未提升
```

---

> Generated by Claude Code — Markdown Ready
