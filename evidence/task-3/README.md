# 題目三人工驗收

## 驗證目的

確認 Browser Workflow 能在固定公司官網內完成研究、保存逐步操作紀錄，並在執行前拒絕超出允許範圍的任務。

## 實際結果

| 案例 | 驗證內容 | 結果 |
|---|---|---|
| [完整官網研究](question-3-01/README.md) | 查詢公司介紹、主要產品與福利制度 | 通過。造訪三個官方頁面，三類主題均取得證據。 |
| [非法網域拒絕](question-3-02/README.md) | 要求前往 YouTube 查詢 | 通過。網域驗證階段即拒絕，未啟動 Chromium、未建立 Browser Run、未造訪頁面。 |

## Browser Run Logs

- [完整官網研究操作紀錄](question-3-01/run-logs.md)
- [非法網域拒絕紀錄](question-3-02/run-logs.md)
- [整合流程操作紀錄](../integrated/integrated-01/run-logs.md)

## 官網來源核對

- [`question-3-01` 官網證據框選圖](question-3-01/source-verification.md)

## 結論

通過。Browser Agent 的成功與拒絕路徑都可由實際網址、操作順序及停止原因直接核對。
