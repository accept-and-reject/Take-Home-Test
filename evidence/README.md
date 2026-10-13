# 人工驗收證據

本目錄保存 2026-10-13 由使用者親自執行並確認的展示結果。證據來自同一次 Demo Run：`20261013-053628-5402`。

## 驗收結論

- [整合流程](integrated/README.md)：通過。Agent 先查詢會議待辦，使用者明確要求後才執行官網研究。
- [題目一](task-1/README.md)：通過。兩組查詢均提供可核對的 PDF／PPTX 來源。
- [題目三](task-3/README.md)：通過。完整官網研究成功，非法網域在啟動 Chromium 前被拒絕。

## 證據內容

- `task-1/<Scenario>/README.md`：題目一的實際使用者輸入、Agent 回答與 Tool 執行摘要。
- `task-1/<Scenario>/source-verification.md` 與 `images/`：Agent 回答引用的 PDF／PPTX 來源核對；紅框標示具體證據位置。
- `task-3/<Scenario>/README.md`：題目三的實際輸入與 Browser Task 結果。
- `task-3/question-3-01/source-verification.md` 與 `images/`：Browser Task 輸出中三組「證據」的官網來源核對與框選圖。
- `task-3/<Scenario>/run-logs.md`：題目三的逐步瀏覽器操作或執行前拒絕紀錄。
- `integrated/<Scenario>/README.md`：聯合驗證的實際使用者輸入、Agent 回答與 Tool 執行摘要。
- `integrated/<Scenario>/source-verification.md` 與 `images/`：第一輪 PDF／PPTX 回答與第二輪官網研究的來源核對及框選圖。
- `integrated/<Scenario>/run-logs.md`：使用者確認後執行官網研究的逐步瀏覽器操作紀錄。

本目錄未收錄 API Key、Token、內部 UUID、Runtime Database、完整 Tool JSON 或其他與成果判斷無關的診斷資料。
