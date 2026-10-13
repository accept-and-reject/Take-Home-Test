# AI Application Engineer Home Test

## 摘要

這是一個可公開驗證的「專案會議行動助理」，系統將跨週 PDF／PPTX 會議紀錄轉為可搜尋、可追溯的 Knowledge Base，透過 Remote MCP 回答專案問題；另外提供受限的 Browser Agent，在指定公司官方網站內完成公開資訊研究並留下逐步 Run Logs

本作品完成兩項考題：

- **題目一：Unstructured Data Pipeline & Remote MCP Server**
- **題目三：Browser Automation Agent Task**

### 系統流程概覽

> **用途：** 快速說明題目一、題目三各自的驗證路徑，以及兩題如何串接驗證

- 題目一與題目三分成兩個 Service，可以獨立驗證
- 題目一與題目三可以串接使用，形成聯合驗證

```text
題目一獨立：模擬企業 PDF／PPTX ──> Knowledge／MCP Service ──> 可追溯回答

題目三獨立：Direct Browser Client ──> Browser Executor Service ──> 指定公司官方網站

聯合驗證：模擬企業 PDF／PPTX ──> Knowledge／MCP Service ──> Browser Executor Service ──> 指定公司官方網站
```

## 公開入口

> Cloud Service 已部署於 Zeabur；API Token 不公開於 Repository

- **Repository：** [https://github.com/accept-and-reject/Take-Home-Test](https://github.com/accept-and-reject/Take-Home-Test)
- **Knowledge Service：** [https://knowledge-mcp.zeabur.app](https://knowledge-mcp.zeabur.app)
- **Remote MCP：** [https://knowledge-mcp.zeabur.app/mcp](https://knowledge-mcp.zeabur.app/mcp)
- **Browser Service：** [https://browser-executor.zeabur.app](https://browser-executor.zeabur.app)

## 人工驗收證據

> **用途：** 讓審查者不必先啟動系統，也能直接查看一次真實執行的問答、來源核對與 Browser Run Logs

- [查看整合流程、題目一與題目三的人工驗收結果](./evidence/README.md)

## Key assumptions

> **用途：** 說明作品主動採用的執行條件和業務範圍，讓審查者知道本次實作的限縮條件

### 環境

> **用途：** 說明作品的執行條件、外部連線需求與 Secret 提供方式

- 本地執行環境限定為 Ubuntu 24.04（Linux）
- 本地的 Docker 與 Docker Compose 已安裝並可執行
- 已準備具有可用額度的 OpenAI API Key；ChatGPT Plus 不包含應用程式 API 額度
- 主機可透過 HTTPS 連線 OpenAI API 與瑞鼎科技官網
- 設定腳本不會安裝或修改主機層級工具
- API Key 與 Token 只透過 `.env` 或部署平台 Secret 提供，不寫入 Repository

### 業務情境

> **用途：** 簡要說明作品服務的對象、使用方式和實作範圍

- **使用者：** 需要追蹤專案狀態的專案協調人或團隊成員
- **資料：** Knowledge Base 使用自行建立的兩週模擬企業資料，不包含真實公司機密或個人資料
  - 固定模擬資料位於 [`test-data/meetings/base/`](./test-data/meetings/base/)
  - 每週各包含一份會議 PDF 和一份專案週報 PPTX
  - 文件同時包含多個專案，決策與待辦會在後續會議更新
- **情境一 — 理解特定專案的會議進度：** 使用者想確認特定專案的跨週決策、進度變化、未完成事項及來源
- **情境二 — 執行選定的公司資訊研究事項：** 使用者確認瑞鼎科技研究待辦仍未完成，並決定推進工作
  - 題目三也可以透過固定公司研究任務獨立啟動，不必先查詢 Knowledge Base
- **限制：**
  - Knowledge 查詢只回答已匯入文件能支持的內容
  - Knowledge Base 只處理 PDF／PPTX
  - Browser 只研究瑞鼎科技官網的公司介紹、主要產品與福利制度
    - 取得完整證據、合法連結耗盡、達到五頁或發生錯誤時停止，缺少證據的主題標示為未找到
    - 研究結果也不會回寫原始文件或 Knowledge Base
  - 單純查詢不會觸發 Browser

## Run / Verify

> **用途：** 提供從取得專案、準備執行環境到驗證展示成果的最短操作路徑

### 取得 Repository

```bash
git clone https://github.com/accept-and-reject/Take-Home-Test.git
cd Take-Home-Test
```

### 建立或連線執行環境

請依驗證環境選擇 Local 或 Cloud 設定流程。

**Local**

在 Repository Root 執行：

```bash
./scripts/setup.sh
```

1. 在「是否使用已部署的 Cloud Service 驗證？」輸入 `n`
2. 輸入 OpenAI API Key；輸入內容不會顯示於終端
3. Knowledge／Browser Port 可直接按 Enter 使用 `8001`／`8002`
4. 腳本會自動產生兩個 Service Token、建立並啟動兩個 Service、等待 Health，並匯入四份固定測試文件

**Cloud：** 本作品的 Cloud Service 部署於 Zeabur

確認兩個 Service 已部署並取得公開網址與 Token 後，在 Repository Root 執行：

```bash
./scripts/setup.sh
```

1. 在「是否使用已部署的 Cloud Service 驗證？」輸入 `y`
2. 輸入 Knowledge／Browser Public URL、OpenAI API Key、MCP Bearer Token 與 Browser API Token；Secret 輸入不會顯示於終端
   - 驗證公開部署的系統所需的 MCP／Browser Token 會在交付 Email 的私人 README 中另行提供
   - OpenAI API Key 不會提供
3. 腳本只會建立本機展示 Client 使用的 `.env`，不會部署、修改或重新匯入 Cloud Service

### 建立展示 Client

完成 Local 或 Cloud 設定後，在 Repository Root 執行：

```bash
docker build -f demo/Dockerfile -t ai-home-test-demo .
```

若使用 Cloud Service，服務可能因閒置而休眠，所以執行展示前，請依序喚醒 Browser Service 與 Knowledge Service：

```bash
curl -fsS "https://browser-executor.zeabur.app/health"
curl -fsS "https://knowledge-mcp.zeabur.app/health"
```

執行全部五組展示情境：

```bash
docker run --rm \
  --env-file .env \
  --add-host host.docker.internal:host-gateway \
  --user "$(id -u):$(id -g)" \
  -v "$PWD/logs/demo:/app/logs/demo" \
  ai-home-test-demo
```

每組情境會在 Terminal 顯示使用者輸入與最終結果，並將主要輸出保存至：

```text
logs/demo/<run-id>/<scenario-id>/transcript.md
```

含 Browser Task 的情境會另外產生主管可讀的逐步操作紀錄：

```text
logs/demo/<run-id>/<scenario-id>/run-logs.md
```

展示 Client 限制、五組 Scenario 的用途、單獨執行方式與預期結果，請見 [Demo 驗證指南](./demo/README.md)

## AI／Agent workflow

> **用途：** 說明 AI 如何參與開發

**省流：** 本專案採用文件驅動的 AI 協作方式。AI 先將需求整理成可確認的文件，再依人工確認結果進行實作；使用者負責關鍵決策、成果驗收、正式部署

**開發流程：**

```text
空白專案
→ 參考資料整理
→ 開發文件
→ 人工確認
→ Commit 規劃
→〔AI 實作 → 人工驗收 → Commit〕逐一完成 Cxx
→ 系統交付
```

**AI 參與位置：**

- **規劃階段：** AI 依固定規範與文件模板，逐步建立需求、設計、測試、交付文件
- **決策階段：** AI 在需要做出重要決策時整理候選方案及差異，待使用者確認後再繼續
- **實作階段：** AI 將確認內容拆成 `Cxx`，依序建立程式碼、測試資料及操作腳本
- **驗收階段：** AI 提供測試方式與預期結果；使用者執行並判定，通過後由 AI 建立 Commit
- **交付階段：** AI 準備交付內容，但不代替使用者正式部署
