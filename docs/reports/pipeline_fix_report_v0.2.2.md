# Pipeline Fix Report — v0.2.2

> Content-Driven Deep Analysis Refactor

## 1. 問題總結

本次修正針對 v0.2.1 中 deep_analysis.md 品質低落的三層根因：

### 1.1 Enrichment 層
- HN items 全文抓取成功率約 85%，少數文章（GPT-5.3、immigration 等）抓取失敗
- 此層本次未修改，屬已知限制

### 1.2 Entity Extraction 層
- 會把地名（Taklamakan）、普通詞（Desert、Sign）、機構縮寫（FAA、Paso）抽成實體
- 這些「實體」被下游 deep_analyzer 誤用為利益相關方

### 1.3 Deep Analyzer Fallback（根因）
- `_get_stakeholders(category, entities)` 將 entity 以 round-robin 方式塞進固定角色模板
- 產出荒謬結果如 `"能源企業（如 Taklamakan）"`、`"政府／監管（如 Desert）"`、`"消費者（如 Sign）"`
- `summary_zh` 被截斷至 200 chars，導致機制選擇資訊不足
- `key_points` 僅取 3 條，fallback 分析材料貧乏

---

## 2. 本次修正（逐檔案）

### `core/ai_core.py` — 最小改動
| 改動 | 說明 |
|------|------|
| `summary_zh = body[:200]` → `body[:2000]` | 提供 10 倍文本給 fallback 機制選擇 |
| `key_points = [...][:3]` → `[...][:8]` | 更多真實句子作為分析材料 |

### `core/deep_analyzer.py` — 重點改寫

#### 新增 2 個 lookup tables
- **`_CATEGORY_CONTEXT`**（11 categories）：每個類別一句 stakeholder 利益描述，不使用 entity 名稱
- **`_CATEGORY_METRICS`**（11 categories）：每個類別 4-5 個 domain-specific 觀察指標

#### 擴充 `_MECHANISM_KEYWORDS`
- 供應鏈：`energy`, `material`, `resource`, `mineral`, `能源`, `原料`
- 監管槓桿：`climate`, `carbon`, `emission`, `health`, `medical`, `氣候`, `碳排`
- 激勵設計：`subsidy`, `tax credit`, `補貼`, `減免`
- 採用曲線：`market`, `consumer`, `deployment`, `市場`, `商用`

#### 重寫 8 個 fallback functions
| 函式 | 改動類型 | 關鍵變化 |
|------|----------|----------|
| `_fallback_forces_incentives` | 全面重寫 | 使用 key_points + `_CATEGORY_CONTEXT`，移除 `_get_stakeholders()` |
| `_fallback_first_principles` | 部分重寫 | `"對 {entity_str} 而言"` → `"根據「{key_points[0][:80]}」"` |
| `_fallback_derivable_effects` | 重寫 | 以 key_points + category 為主詞，不再使用 entity 名稱 |
| `_fallback_speculative_effects` | 重寫 | `"若 {entity_str} 在..."` → `"若「{key_points[0][:50]}」所述趨勢..."` |
| `_fallback_opportunities` | 重寫 | 3 條內容驅動機會：category+mechanism / key_points 趨勢 / 市場缺口 |
| `_fallback_observation_metrics` | 最簡重寫 | 直接回傳 `_CATEGORY_METRICS[category][:5]` |
| `_fallback_counter_risks` | 重寫 | 使用 key_points 趨勢 + category 緩解措施方向 |
| `_fallback_strategic_outlook` | 小幅重寫 | `"entity 的動態"` → `"此事件對 category 領域的影響"` + 引用 key_points |

#### 刪除 dead code
- `_STAKEHOLDER_TEMPLATES` dict（約 30 行）
- `_get_stakeholders()` function（約 50 行）

### `tests/test_deep_analysis.py` — 測試更新

| 測試 | 狀態 | 說明 |
|------|------|------|
| `test_opportunities_not_generic` | 修改 | 從「引用 entity」改為「引用 key_point 前 30 字」 |
| `test_non_tech_content_no_absurd_roles` | 新增 | Taklamakan 文章不得出現 `"（如 Taklamakan）"` 等荒謬映射 |
| `test_empty_key_points_graceful_fallback` | 新增 | 空 key_points + 空 body 不崩潰、輸出有效 |

### `tests/golden_snapshot.json`
- 舊檔刪除，由 `test_golden_snapshot_structure` 自動重生

---

## 3. 測試與驗收結果

### Ruff Lint
```
> venv/Scripts/python.exe -m ruff check .
All checks passed!
```

### Pytest
```
> venv/Scripts/python.exe -m pytest tests/ -v
============================= test session starts =============================
collected 39 items

tests/test_article_fetch.py ...........                                  [ 28%]
tests/test_classification.py ......                                      [ 43%]
tests/test_deep_analysis.py ..........                                   [ 69%]
tests/test_entity_extraction.py .........                                [ 92%]
tests/test_text_clean.py ...                                             [100%]

============================= 39 passed in 1.10s ==============================
```

---

## 4. Before / After 範例

### Taklamakan Desert 文章 — 力場分析

**Before (v0.2.1)**:
```
力場分析：
- 能源企業（如 Taklamakan）：在轉型壓力下尋求新利潤來源（約束：轉型投資與股東期望）
- 政府／監管（如 Desert）：推動減碳目標與能源安全（約束：政治可行性與國際協定）
- 消費者（如 Sign）：期望更低成本與更永續的能源選擇（約束：價格敏感度與行為慣性）
```

**After (v0.2.2)**:
```
力場分析：
- 主要動態：China has planted so many trees around the Taklamakan Desert
- 背景脈絡：Scientists recorded a measurable change in local climate
- 利益相關方：能源產業在轉型壓力下尋求新利潤來源；政府推動減碳與能源安全；消費者期望永續選擇
```

### Taklamakan Desert 文章 — 第一性原理分析

**Before (v0.2.1)**:
```
核心機制：採用曲線（adoption curve）
該事件的底層邏輯與「採用曲線（adoption curve）」直接相關。對 Taklamakan、Desert、Central Asian 而言，
目前處於採用曲線的哪個階段將決定策略重心...
```

**After (v0.2.2)**:
```
核心機制：監管槓桿（regulatory leverage）
該事件的底層邏輯與「監管槓桿（regulatory leverage）」直接相關。根據「China has planted so many trees around the Taklamakan Desert」，
監管態勢直接影響可行性與時程，政策變動可能重塑競爭格局。
```

### Taklamakan Desert 文章 — 觀察指標

**Before (v0.2.1)**:
```
- Taklamakan 的公開產品更新或版本發布頻率
- 相關領域的季度融資總額與交易數量
- 技術社群（GitHub stars、HN 討論數）的參與度趨勢
- Desert 的市場份額或用戶數變化
- 監管機構相關政策或指導文件的發布動態
```

**After (v0.2.2)**:
```
- 碳排放監測趨勢
- 再生能源裝機量與發電占比
- 碳交易價格與市場規模
- 綠色投資流入金額
- 氣候政策承諾與執行進度
```

---

## 5. 回滾方式

若需回滾至 v0.2.1，按以下檔案層級操作：

### `core/ai_core.py`
```python
# 還原 chain_a_fallback() 中兩行：
summary = body[:200] + ("..." if len(body) > 200 else "")   # 原為 body[:2000]
key_points = [...][:3]                                       # 原為 [:8]
```

### `core/deep_analyzer.py`
- 還原 `_STAKEHOLDER_TEMPLATES` dict 和 `_get_stakeholders()` function
- 刪除 `_CATEGORY_CONTEXT` 和 `_CATEGORY_METRICS` dicts
- 還原 8 個 `_fallback_*` functions 為原始版本（使用 entity_str + _get_stakeholders）
- 還原 `_MECHANISM_KEYWORDS` 中新增的 keywords

### `tests/test_deep_analysis.py`
- 還原 `test_opportunities_not_generic` 為 entity-based 斷言
- 刪除 `test_non_tech_content_no_absurd_roles`
- 刪除 `test_empty_key_points_graceful_fallback`
- 刪除 `tests/golden_snapshot.json` 並重生

### Git 快速回滾
```bash
git log --oneline -5          # 找到 v0.2.1 的 commit hash
git revert <commit-hash>      # 安全回滾（保留歷史）
```
