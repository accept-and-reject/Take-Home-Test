# 題目一人工驗收

## 驗證目的

確認 Agent 能透過 Remote MCP 查詢跨週 PDF／PPTX 內容，回答決策與進度變化，並提供可回到原始文件的位置。

## 實際結果

| 案例 | 驗證內容 | 結果 |
|---|---|---|
| [發布決策查詢](question-1-01/README.md) | 查詢 2026-09-18 的 Display Driver v2.4 RC 發布決策 | 通過。回答暫停發布、20 次測試條件及 PDF／PPTX 來源。 |
| [跨週進度與決策](question-1-02/README.md) | 整理 2026-09-18 至 2026-09-25 的變化 | 通過。回答從 14/20、暫停發布到 20/20、恢復發布的變化及跨週來源。 |

## 原始文件核對

每個 Scenario 的資料夾都包含該次回答與專屬來源核對：

- [`question-1-01` 來源核對](question-1-01/source-verification.md)
- [`question-1-02` 來源核對](question-1-02/source-verification.md)

## 結論

通過。Agent 的重要結論能回到實際 PDF 頁面與 PPTX 投影片核對，不只是無來源摘要。
