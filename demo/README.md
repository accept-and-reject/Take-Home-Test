# Demo 驗證指南

本文件補充專案 README 中五組固定展示情境的用途、單獨執行方式與預期結果。

## 展示 Client 限制

- `run_demo.py` 是驗證 Client，不是系統 Service 或正式使用者介面。
- 只執行五組固定 Scenario；可以一次執行全部情境，或用 `--scenario` 選擇一組，不提供任意問題輸入
- 題目一與聯合驗證使用 MCP Agent；題目三獨立驗證使用 Direct Browser Client。
- 每組 MCP Agent Scenario 使用獨立的 Session-only Memory；情境或程式結束後即清除，不使用長期記憶，也不將對話內容寫回 Knowledge Base。
- Transcript 與 Browser Run Logs 只保存在本機 `logs/demo/`，不會自動提交至 Repository。

## 整合：從會議待辦執行官網研究

| Scenario | 驗證內容 | 預期結果 |
|---|---|---|
| `integrated-01` | 先查詢 Display Driver 的瑞鼎科技研究待辦，再由使用者回答「好，幫我執行這個待辦」 | Agent 只在明確同意後呼叫 Browser Service，回傳官網研究結果與完整操作紀錄 |

整合流程不會把 Browser 結果寫回原始會議文件或 Knowledge Base。

## 題目一：會議知識查詢

| Scenario | 驗證內容 | 預期結果 |
|---|---|---|
| `question-1-01` | 查詢 2026-09-18 的 Display Driver 發布決策 | 回答發布決策，並附 PDF 頁面或 PPTX 投影片來源 |
| `question-1-02` | 整理 2026-09-18 至 2026-09-25 的跨週變化 | 說明進度與決策變化，並保留可核對來源 |

題目一不依賴 Browser Service；若 Browser Service 不可用，會議查詢仍應正常回傳可追溯結果。

## 題目三：受限 Browser Agent

| Scenario | 驗證內容 | 預期結果 |
|---|---|---|
| `question-3-01` | 只使用瑞鼎科技官網查詢公司介紹、主要產品與福利制度 | 回傳繁體中文摘要、官網證據、判斷理由、來源 URL 與 Run Logs |
| `question-3-02` | 要求改往 YouTube 查詢 | 在啟動 Chromium 前拒絕非法網域，不建立 Browser Run |

題目三可以在 Knowledge Service 不可用時獨立執行。Browser Agent 固定只允許瑞鼎科技官網、三類研究主題及最多五個頁面；找不到足夠官網證據時會明確標示未找到，不使用其他網站補答案。
