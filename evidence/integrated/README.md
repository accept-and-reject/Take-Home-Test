# 聯合驗證人工驗收

## 驗證目的

確認 Agent 能先從 Knowledge Base 取得公司研究待辦，並只在使用者明確要求後交由 Browser Service 執行。

## 實際結果

| 案例 | 驗證內容 | 結果 |
|---|---|---|
| [從會議待辦執行官網研究](integrated-01/README.md) | 先查詢公司研究待辦，再由使用者確認執行 | 通過。第一輪只查詢 Knowledge Base；第二輪明確同意後才執行 Browser Workflow，三類研究主題均取得官網證據。 |

## 來源核對

- [`integrated-01` 會議文件與官網研究來源核對](integrated-01/source-verification.md)

## Browser Run Logs

- [`integrated-01` 瀏覽器操作紀錄](integrated-01/run-logs.md)

## 結論

通過。題目一與題目三可以獨立驗證，也能以會議待辦形成可追溯的聯合驗證流程。
