#!/usr/bin/env bash

# 用途：從 Project 根目錄執行一次隔離的全系統功能檢查。
# 主要內容：建立暫時 Compose Project、逐 Case 保存 Log、切換雙服務狀態，
#           等待人工完成內容與 Codex 驗證後，再移除本次測試產生的 Runtime 資源。
# 使用方式：app/scripts/run_manual_checks.sh

set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
RUN_LOG_DIR="${APP_DIR}/logs/system-check/${RUN_ID}"
TEST_PROJECT_NAME="ai-home-test-check-${RUN_ID}"
CURRENT_PHASE="initialization"
CLEANUP_REQUIRED=0
MANUAL_MCP_URL=""
BROWSER_CONTAINER_ID=""

export KNOWLEDGE_PORT=4000
export BROWSER_PORT=4001

cd "${APP_DIR}"
mkdir -p "${RUN_LOG_DIR}"

compose() {
  docker compose --project-name "${TEST_PROJECT_NAME}" "$@"
}

cleanup_after_exit() {
  [[ "${CLEANUP_REQUIRED}" -eq 1 ]] || return 0
  mkdir -p "${RUN_LOG_DIR}/runtime-isolation"
  compose down --volumes --remove-orphans --rmi local \
    >>"${RUN_LOG_DIR}/runtime-isolation/cleanup.log" 2>&1 || true
}

on_error() {
  local exit_code=$?
  trap - ERR
  printf '\n[FAILED] %s (exit code %s)\n' \
    "${CURRENT_PHASE}" "${exit_code}" >&2
  printf '[LOGS] %s\n' "${RUN_LOG_DIR}" >&2
  exit "${exit_code}"
}

trap cleanup_after_exit EXIT
trap on_error ERR

fail() {
  printf '\n[FAILED] %s\n' "$1" >&2
  printf '[LOGS] %s\n' "${RUN_LOG_DIR}" >&2
  exit 1
}

section() {
  CURRENT_PHASE="$1"
  printf '\n== %s ==\n' "$1"
}

run_check() {
  local log_name="$1"
  local description="$2"
  shift 2

  local log_path="${RUN_LOG_DIR}/${log_name}.log"
  local exit_code
  mkdir -p "$(dirname -- "${log_path}")"
  if "$@" >"${log_path}" 2>&1; then
    printf '[OK] %s\n' "${description}"
    return 0
  else
    exit_code=$?
  fi

  printf '[FAILED] %s；詳細輸出：%s\n' \
    "${description}" "${log_path}" >&2
  printf '%s\n' '--- failure output (last 40 lines) ---' >&2
  tail -n 40 "${log_path}" >&2
  return "${exit_code}"
}

run_hidden_check() {
  local log_name="$1"
  local description="$2"
  shift 2

  local log_path="${RUN_LOG_DIR}/${log_name}.log"
  local exit_code
  mkdir -p "$(dirname -- "${log_path}")"
  if "$@" >"${log_path}" 2>&1; then
    return 0
  else
    exit_code=$?
  fi

  printf '[FAILED] %s；詳細輸出：%s\n' \
    "${description}" "${log_path}" >&2
  printf '%s\n' '--- failure output (last 40 lines) ---' >&2
  tail -n 40 "${log_path}" >&2
  return "${exit_code}"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "找不到必要指令：$1"
}

env_value() {
  local key="$1"
  awk -F= -v key="${key}" '
    $1 == key {
      sub(/^[^=]*=/, "")
      print
      exit
    }
  ' .env
}

check_required_env() {
  local key value
  for key in \
    OPENAI_API_KEY \
    MCP_BEARER_TOKEN \
    BROWSER_EXECUTOR_TOKEN \
    DEMO_API_TOKEN \
    START_URL \
    ALLOWED_DOMAIN
  do
    value="$(env_value "${key}")"
    [[ -n "${value}" ]] || fail ".env 缺少有效的 ${key}"
    case "${value}" in
      *replace-with*|*change-me*|*your-key*)
        fail ".env 的 ${key} 仍是範例值"
        ;;
    esac
  done
}

wait_for_service_health() {
  local service="$1"
  local attempt
  for attempt in {1..45}; do
    if compose exec -T "${service}" \
      python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" \
      >/dev/null 2>&1
    then
      return 0
    fi
    sleep 2
  done
  return 1
}

check_environment() {
  section "Environment"
  require_command docker
  [[ -f .env ]] || fail "找不到 app/.env"
  check_required_env

  run_check \
    "runtime-isolation/environment/docker-compose" \
    "Docker Compose 可用" \
    docker compose version
  run_check \
    "runtime-isolation/environment/compose-config" \
    "隔離測試用 Compose 設定有效" \
    compose config --quiet

  local fixture
  for fixture in \
    test-data/meetings/base/weekly-meeting-01.pdf \
    test-data/meetings/base/weekly-report-01.pptx \
    test-data/meetings/base/weekly-meeting-02.pdf \
    test-data/meetings/base/weekly-report-02.pptx \
    test-data/meetings/partial/valid-meeting.pdf \
    test-data/meetings/partial/corrupt-meeting.pptx
  do
    [[ -s "${fixture}" ]] || fail "固定測試資料不存在或為空：${fixture}"
  done
  printf '[OK] 六份固定測試資料均可用。\n'
}

build_isolated_runtime() {
  section "Isolated runtime"
  CLEANUP_REQUIRED=1
  run_check \
    "runtime-isolation/environment/build-services" \
    "建立隔離測試用 Service Images" \
    compose build knowledge-mcp-service browser-executor-service
}

use_knowledge_only() {
  section "Service independence"
  run_check \
    "runtime-isolation/knowledge-only/start" \
    "Knowledge-only：只啟動 Knowledge Service" \
    compose up -d --no-deps knowledge-mcp-service
  run_check \
    "runtime-isolation/knowledge-only/health" \
    "Knowledge-only：Browser 不可用時仍可啟動" \
    wait_for_service_health knowledge-mcp-service
}

test_ingestion() {
  section "Flow 1 - ingestion"
  run_check \
    "flow-1-ingestion/case-01" \
    "Case 1：四份跨週 PDF/PPTX 可完成匯入" \
    compose exec -T knowledge-mcp-service \
    python scripts/ingest_meetings.py \
    --input test-data/meetings/base
  run_check \
    "flow-1-ingestion/case-02" \
    "Case 2：重複匯入不會使流程失敗" \
    compose exec -T knowledge-mcp-service \
    python scripts/ingest_meetings.py \
    --input test-data/meetings/base
  run_check \
    "flow-1-ingestion/case-03" \
    "Case 3：單一損壞來源不阻止有效文件" \
    compose exec -T knowledge-mcp-service \
    python scripts/ingest_meetings.py \
    --input test-data/meetings/partial \
    --data-dir /tmp/system-check-partial
}

test_knowledge_independently() {
  section "Flow 2 - Knowledge MCP"
  run_check \
    "flow-2-knowledge-mcp/case-01" \
    "Case 1：跨週查詢可回傳來源與 Locator" \
    compose exec -T knowledge-mcp-service \
    sh -c 'python scripts/test_mcp.py --url http://localhost:8000/mcp --token "$MCP_BEARER_TOKEN" --case flow2-case1'
  run_check \
    "flow-2-knowledge-mcp/case-02" \
    "Case 2：可依 ActionItem ID 取得待辦與跨週證據" \
    compose exec -T knowledge-mcp-service \
    sh -c 'python scripts/test_mcp.py --url http://localhost:8000/mcp --token "$MCP_BEARER_TOKEN" --case flow2-case2'
  run_check \
    "flow-2-knowledge-mcp/case-03" \
    "Case 3：證據不足時不產生無來源答案" \
    compose exec -T knowledge-mcp-service \
    sh -c 'python scripts/test_mcp.py --url http://localhost:8000/mcp --token "$MCP_BEARER_TOKEN" --case flow2-case3'
  run_check \
    "flow-2-knowledge-mcp/case-04" \
    "Case 4：Browser Service 停止時題目一仍可查詢" \
    compose exec -T knowledge-mcp-service \
    sh -c 'python scripts/test_mcp.py --url http://localhost:8000/mcp --token "$MCP_BEARER_TOKEN" --case flow2-case4'
  run_check \
    "flow-2-knowledge-mcp/case-05" \
    "Case 5：拒絕未授權的 MCP Client" \
    compose exec -T knowledge-mcp-service \
    sh -c 'python scripts/test_mcp.py --url http://localhost:8000/mcp --token "$MCP_BEARER_TOKEN" --case flow2-case5'
}

use_browser_only() {
  run_check \
    "runtime-isolation/browser-only/stop-knowledge" \
    "Browser-only：停止 Knowledge Service" \
    compose stop knowledge-mcp-service
  run_check \
    "runtime-isolation/browser-only/start" \
    "Browser-only：只啟動 Browser Service" \
    compose up -d --no-deps browser-executor-service
  run_check \
    "runtime-isolation/browser-only/health" \
    "Browser-only：Knowledge 不可用時仍可啟動" \
    wait_for_service_health browser-executor-service
}

prepare_knowledge_flows() {
  run_hidden_check \
    "runtime-isolation/flow-preparation/stop-browser" \
    "準備 Flow 1、Flow 2 時停止 Browser Service" \
    compose stop browser-executor-service
  run_hidden_check \
    "runtime-isolation/flow-preparation/start-knowledge" \
    "準備 Flow 1、Flow 2 時啟動 Knowledge Service" \
    compose up -d --no-deps knowledge-mcp-service
  run_hidden_check \
    "runtime-isolation/flow-preparation/knowledge-health" \
    "準備 Flow 1、Flow 2 時確認 Knowledge Service Health" \
    wait_for_service_health knowledge-mcp-service
}

prepare_browser_flow() {
  run_hidden_check \
    "runtime-isolation/flow-preparation/stop-knowledge" \
    "準備 Flow 3 時停止 Knowledge Service" \
    compose stop knowledge-mcp-service
  run_hidden_check \
    "runtime-isolation/flow-preparation/start-browser" \
    "準備 Flow 3 時啟動 Browser Service" \
    compose up -d --no-deps browser-executor-service
  run_hidden_check \
    "runtime-isolation/flow-preparation/browser-health" \
    "準備 Flow 3 時確認 Browser Service Health" \
    wait_for_service_health browser-executor-service
}

test_browser_flow() {
  section "Flow 3 - Browser Task"
  run_check \
    "flow-3-browser-task/case-01" \
    "Case 1：Knowledge 不可用時可直接完成 Browser Task" \
    compose exec -T browser-executor-service \
    python scripts/test_browser_task.py \
    --url http://localhost:8000 \
    --case flow3-case1
  run_check \
    "flow-3-browser-task/case-02" \
    "Case 2：拒絕非指定官網與無效請求" \
    compose exec -T browser-executor-service \
    python scripts/test_browser_task.py \
    --url http://localhost:8000 \
    --case flow3-case2
  run_check \
    "flow-3-browser-task/case-03" \
    "Case 3：頁數上限為一時停止新增頁面" \
    compose exec -T browser-executor-service \
    python scripts/test_browser_task.py \
    --url http://localhost:8000 \
    --case flow3-case3

  run_hidden_check \
    "runtime-isolation/integrated/start-services" \
    "啟動 Knowledge 與 Browser Services" \
    compose up -d --no-deps knowledge-mcp-service browser-executor-service
  run_hidden_check \
    "runtime-isolation/integrated/knowledge-health" \
    "整合狀態下 Knowledge Service Health 可用" \
    wait_for_service_health knowledge-mcp-service
  run_hidden_check \
    "runtime-isolation/integrated/browser-health" \
    "整合狀態下 Browser Service Health 可用" \
    wait_for_service_health browser-executor-service
  run_check \
    "flow-3-browser-task/case-04" \
    "Case 4：由 ActionItem 完成整合並保留追溯鏈" \
    compose exec -T knowledge-mcp-service \
    sh -c 'python scripts/test_mcp.py --url http://localhost:8000/mcp --token "$MCP_BEARER_TOKEN" --case flow3-case4'

  run_hidden_check \
    "runtime-isolation/integrated/stop-browser" \
    "停止 Browser Service 以驗證整合降級" \
    compose stop browser-executor-service
  run_check \
    "flow-3-browser-task/case-05" \
    "Case 5：Browser 不可用時整合功能明確降級" \
    compose exec -T knowledge-mcp-service \
    sh -c 'python scripts/test_mcp.py --url http://localhost:8000/mcp --token "$MCP_BEARER_TOKEN" --case flow3-case5'
}

detect_manual_mcp_url() {
  local published_port manual_host
  published_port="$(compose port knowledge-mcp-service 8000 | awk -F: 'NF { port=$NF } END { print port }')"
  [[ "${published_port}" =~ ^[0-9]+$ ]] || return 1

  manual_host="${MANUAL_TEST_HOST:-}"
  if [[ -z "${manual_host}" ]]; then
    manual_host="$(hostname -I 2>/dev/null | awk '{ print $1 }')"
  fi
  [[ -n "${manual_host}" ]] || manual_host="127.0.0.1"

  printf 'http://%s:%s/mcp\n' "${manual_host}" "${published_port}"
}

prepare_manual_review_runtime() {
  section "Manual review preparation"
  run_check \
    "runtime-isolation/manual-review/start-services" \
    "恢復 Knowledge 與 Browser Services 供人工驗證" \
    compose up -d --no-deps knowledge-mcp-service browser-executor-service
  run_check \
    "runtime-isolation/manual-review/knowledge-health" \
    "人工驗證用 Knowledge Service Health 可用" \
    wait_for_service_health knowledge-mcp-service
  run_check \
    "runtime-isolation/manual-review/browser-health" \
    "人工驗證用 Browser Service Health 可用" \
    wait_for_service_health browser-executor-service

  MANUAL_MCP_URL="$(detect_manual_mcp_url)" \
    || fail "無法取得人工驗證用 Knowledge MCP URL"
  BROWSER_CONTAINER_ID="$(compose ps -q browser-executor-service)"
  [[ -n "${BROWSER_CONTAINER_ID}" ]] \
    || fail "無法取得人工驗證用 Browser Service Container ID"
}

cleanup_isolated_runtime() {
  if compose down --volumes --remove-orphans --rmi local; then
    CLEANUP_REQUIRED=0
    return 0
  fi
  return 1
}

write_manual_review() {
  local flow2_review="${RUN_LOG_DIR}/flow-2-knowledge-mcp/manual-review.txt"
  local flow3_review="${RUN_LOG_DIR}/flow-3-browser-task/manual-review.txt"
  cat >"${flow2_review}" <<EOF
Flow 2 Codex Desktop 人工驗證

這部分無法由 Shell 腳本判定，因為 Tool 選擇、回答組織與批准畫面發生在 Codex Desktop。
請在腳本等待期間使用本次隔離 Runtime；不要先讓腳本進入 Cleanup。

MCP URL：${MANUAL_MCP_URL}
認證：使用 app/.env 內相同的 MCP_BEARER_TOKEN；本檔案不複製 Token 值。
若 Codex Desktop 無法連線偵測到的 IP，請只把 URL 的 Host 改成 Ubuntu VM 可從 Mac 連線的 IP，Port 與 /mcp 保持不變。

準備：開啟新的 Codex 對話，將本次 MCP URL 設為啟用的 Streamable HTTP MCP Server，並確認可看到：
   - search_meetings
   - get_action_item
   - execute_action_item

Case 1：跨週查詢專案進度並取得來源
輸入：
   請整理 Display Driver 專案在 2026-09-18 至 2026-09-25 的討論、決策與未完成事項，並列出可核對的文件來源。

通過條件：Codex 自行選擇 search_meetings；回答涵蓋兩週資料，且關鍵敘述附來源標題與 Locator。最終回答不顯示 UUID、向量分數、原始 Request 或其他內部診斷欄位，也不直接傾印 Tool JSON。
自動化結果比對：${RUN_LOG_DIR}/flow-2-knowledge-mcp/case-01.log

Case 2：依 ActionItem ID 取得可執行待辦與證據
延續 Case 1 的對話，輸入：
   請先顯示公司研究待辦的目前狀態與跨週來源，不要執行它。

通過條件：Codex 在內部使用 Case 1 取得的 ActionItem ID 呼叫 get_action_item；最終回答顯示待辦內容、狀態、瑞鼎科技、允許網域、三個研究主題與跨週來源，而且不顯示 ActionItem ID、Chunk ID 或其他內部識別碼，也沒有啟動 Browser Task。
自動化結果比對：${RUN_LOG_DIR}/flow-2-knowledge-mcp/case-02.log

Case 3：資料不足時不產生無來源答案
輸入：
   這個專案在火星辦公室的正式啟用日期是什麼？請只根據會議文件回答。

通過條件：Codex 使用 search_meetings，並明確回答文件證據不足，不推測日期，也不顯示內部診斷欄位。
自動化結果比對：${RUN_LOG_DIR}/flow-2-knowledge-mcp/case-03.log

Case 4：Browser Service 停止時題目一仍可查詢
在另一個 Terminal 執行：
   docker stop ${BROWSER_CONTAINER_ID}

再次輸入 Case 1 的查詢。
通過條件：Codex 仍能使用 search_meetings 回答，且不產生 Browser 執行結果。
自動化結果比對：${RUN_LOG_DIR}/flow-2-knowledge-mcp/case-04.log

完成後執行下列指令，供 Flow 3 Case 4 使用：
   docker start ${BROWSER_CONTAINER_ID}
   until docker exec ${BROWSER_CONTAINER_ID} python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"; do sleep 2; done

Case 5：拒絕未授權的 MCP Client
本 Case 不需要額外操作 Codex Desktop；確認自動化 Log 中未帶 Token 與錯誤 Token 均為 HTTP 401，Health 仍可用。
自動化結果比對：${RUN_LOG_DIR}/flow-2-knowledge-mcp/case-05.log

人工內容核對：Case 1、2 的會議內容應與原始 PDF／PPTX 語意一致，Locator 可正確回查。原始自動化 Log 應繼續保留 ActionItem ID、Chunk ID、Request ID 與分數，證明只精簡最終回答，沒有刪除內部追溯資料。
EOF

  cat >"${flow3_review}" <<EOF
Flow 3 Codex Desktop 人工驗證

請接續 Flow 2 的人工驗證，使用同一個隔離 Runtime 與 MCP URL：${MANUAL_MCP_URL}

Case 1：Knowledge Service 不可用時直接完成 Browser Task
人工核對自動化 Log：Browser found 的 summary 與 evidence_reason 必須是自然、簡潔的繁體中文；公司名稱、產品名稱與必要外文術語可以保留原文。evidence_quote 必須維持官網原始語言與文字，且實際支持對應 Topic；一般企業價值或產品描述不可被當作 benefits。
自動化結果比對：${RUN_LOG_DIR}/flow-3-browser-task/case-01.log

Case 2：拒絕非指定官網的任務
本 Case 不需要額外操作 Codex Desktop；確認 Log 中請求被拒絕，且沒有建立 BrowserRun。
自動化結果比對：${RUN_LOG_DIR}/flow-3-browser-task/case-02.log

Case 3：頁數上限為一時停止新增頁面
人工核對自動化 Log：visited_page_count 不超過 1，且未取得證據的 Topic 保持 not_found。
自動化結果比對：${RUN_LOG_DIR}/flow-3-browser-task/case-03.log

Case 4：由 ActionItem 完成整合並保留追溯鏈
延續 Flow 2 已取得公司研究 ActionItem 的 Codex 對話，輸入：
   請執行剛才確認的瑞鼎科技公司資訊研究待辦，只使用允許的官方網域，完成後回傳研究結果、官方來源與簡潔的操作摘要。

通過條件：Codex 在內部沿用 ActionItem ID 呼叫 execute_action_item，執行前出現人工批准；批准後只回傳可追溯的研究結果、官網來源與使用者需要的操作摘要，不顯示 ActionItem、BrowserTask、ExecutionRequest、BrowserRun、Finding、ActionLog 的 UUID，也不直接傾印原始 Logs。
自動化結果比對：${RUN_LOG_DIR}/flow-3-browser-task/case-04.log
開發者追溯核對：同一份自動化 Log 必須保留完整 ActionItem → BrowserTask → ExecutionRequest → BrowserRun → Finding／ActionLog 識別鏈。

Case 5：Browser Service 不可用時整合功能明確降級
在另一個 Terminal 執行：
   docker stop ${BROWSER_CONTAINER_ID}

在 Codex 中要求執行相同 ActionItem，再要求查詢 Case 1 的會議內容。
通過條件：Browser 執行明確失敗且不建立假結果；最終回答以簡潔文字說明執行服務不可用，不顯示內部錯誤物件或識別碼；同一對話仍可使用 search_meetings。
自動化結果比對：${RUN_LOG_DIR}/flow-3-browser-task/case-05.log

完成本 Case 後不必重新啟動 Browser Service；回到執行腳本的 Terminal 確認人工驗證完成即可。
EOF

  printf '\n== Manual review still required ==\n'
  printf '[MANUAL] Flow 2：%s\n' "${flow2_review}"
  printf '[MANUAL] Flow 3：%s\n' "${flow3_review}"
  printf '[LOGS] %s\n' "${RUN_LOG_DIR}"
}

wait_for_manual_review() {
  section "Manual review"
  printf '[READY] 隔離環境已保留，Knowledge 與 Browser Services 可供人工驗證。\n'
  printf '[MCP] %s\n' "${MANUAL_MCP_URL}"
  printf '[ORDER] 請先完成 Flow 2，再完成 Flow 3；各檔案均依 Case 1 到 Case 5 排列。\n'
  printf '[WAIT] 完成全部人工驗證後，回到此 Terminal 按 Enter；之後才會清除隔離環境。\n'

  if [[ ! -t 0 ]]; then
    fail "人工驗證階段需要互動式 Terminal"
  fi
  IFS= read -r _
  printf '[OK] 已收到人工驗證完成確認。\n'
}

if [[ "$#" -ne 0 ]]; then
  fail "此腳本不接受參數；請直接執行 scripts/run_manual_checks.sh"
fi

printf '[SYSTEM CHECK] 將使用隔離環境自動驗證全部可程式判定的 Test Case。\n'
printf '[LOGS] %s\n' "${RUN_LOG_DIR}"

check_environment
build_isolated_runtime
use_knowledge_only
use_browser_only
prepare_knowledge_flows
test_ingestion
test_knowledge_independently
prepare_browser_flow
test_browser_flow

printf '\n[AUTO DONE] 全部可程式判定的 Test Case 已通過。\n'
prepare_manual_review_runtime
write_manual_review
wait_for_manual_review

section "Cleanup"
run_check \
  "runtime-isolation/cleanup" \
  "移除測試 Container、Volume、Network 與本次建立的 Images" \
  cleanup_isolated_runtime

printf '\n[DONE] 自動化與人工驗證流程已完成，隔離環境已清除。\n'
