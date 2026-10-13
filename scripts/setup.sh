#!/usr/bin/env bash

# 用途：用單一入口準備本機完整環境，或準備連線已部署 Cloud Service 的 Demo 設定。
# 主要內容：嚴格選擇 Local／Cloud、建立安全的 .env；Local 額外建立雙服務並匯入四份固定文件。
# 使用方式：從 app/ 執行 scripts/setup.sh，依提示輸入 y（Cloud）或 n（Local）。

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${APP_ROOT}/.env"
cd "${APP_ROOT}"

fail() {
  printf '[FAILED] %s\n' "$1" >&2
  exit 1
}

prompt_yes_no() {
  local prompt="$1"
  local answer
  while true; do
    read -r -p "${prompt}" answer
    case "${answer}" in
      y|n)
        printf '%s' "${answer}"
        return 0
        ;;
      *)
        printf '[RETRY] 只能輸入 y 或 n。\n' >&2
        ;;
    esac
  done
}

prompt_secret() {
  local variable_name="$1"
  local description="$2"
  local value

  while true; do
    printf '\n用途：%s\n' "${description}"
    read -r -s -p "${variable_name}（輸入內容不顯示）：" value
    printf '\n'
    if [[ -n "${value}" ]]; then
      printf -v "${variable_name}" '%s' "${value}"
      return 0
    fi
    printf '[RETRY] %s 不可空白。\n' "${variable_name}" >&2
  done
}

prompt_value() {
  local variable_name="$1"
  local description="$2"
  local default_value="$3"
  local value

  printf '\n用途：%s\n' "${description}"
  read -r -p "${variable_name} [預設值：${default_value}；直接按 Enter 使用]：" value
  printf -v "${variable_name}" '%s' "${value:-${default_value}}"
}

prompt_url() {
  local variable_name="$1"
  local description="$2"
  local value

  while true; do
    printf '\n用途：%s\n' "${description}"
    read -r -p "${variable_name}：" value
    if [[ "${value}" =~ ^https?://[^[:space:]]+$ ]]; then
      printf -v "${variable_name}" '%s' "${value%/}"
      return 0
    fi
    printf '[RETRY] 請輸入 http:// 或 https:// 開頭的完整 URL。\n' >&2
  done
}

generate_token() {
  od -An -N24 -tx1 /dev/urandom | tr -d ' \n'
}

load_existing_local_ports() {
  KNOWLEDGE_PORT="$(awk -F= '$1 == "KNOWLEDGE_PORT" {print substr($0, index($0, "=") + 1); exit}' "${ENV_FILE}")"
  BROWSER_PORT="$(awk -F= '$1 == "BROWSER_PORT" {print substr($0, index($0, "=") + 1); exit}' "${ENV_FILE}")"
  KNOWLEDGE_PORT="${KNOWLEDGE_PORT:-8001}"
  BROWSER_PORT="${BROWSER_PORT:-8002}"

  [[ "${KNOWLEDGE_PORT}" =~ ^[0-9]+$ ]] \
    || fail '現有 .env 的 KNOWLEDGE_PORT 必須是整數。'
  [[ "${BROWSER_PORT}" =~ ^[0-9]+$ ]] \
    || fail '現有 .env 的 BROWSER_PORT 必須是整數。'
  [[ "${KNOWLEDGE_PORT}" != "${BROWSER_PORT}" ]] \
    || fail '現有 .env 的 Knowledge 與 Browser Port 不得相同。'
}

write_local_env() {
  prompt_secret \
    OPENAI_API_KEY \
    '供 MCP Server（第一題）、Browser Agent（第三題）與展示 Client 呼叫 OpenAI API；為避免 Secret 外洩，輸入內容不會顯示於終端；ChatGPT Plus 不能取代 API 額度。'
  prompt_value \
    KNOWLEDGE_PORT \
    'MCP Server（第一題）對本機開放的 Port。' \
    '8001'
  prompt_value \
    BROWSER_PORT \
    'Browser Agent（第三題）對本機開放的 Port。' \
    '8002'
  [[ "${KNOWLEDGE_PORT}" =~ ^[0-9]+$ ]] \
    || fail 'KNOWLEDGE_PORT 必須是整數。'
  [[ "${BROWSER_PORT}" =~ ^[0-9]+$ ]] \
    || fail 'BROWSER_PORT 必須是整數。'
  [[ "${KNOWLEDGE_PORT}" != "${BROWSER_PORT}" ]] \
    || fail 'Knowledge 與 Browser Port 不得相同。'

  MCP_BEARER_TOKEN="$(generate_token)"
  BROWSER_API_TOKEN="$(generate_token)"

  umask 077
  {
    printf '%s\n' \
      '# Runtime' \
      'APP_ENV=development' \
      'LOG_LEVEL=DEBUG'
    printf 'KNOWLEDGE_PORT=%s\n' "${KNOWLEDGE_PORT}"
    printf 'BROWSER_PORT=%s\n' "${BROWSER_PORT}"
    printf '%s\n' \
      '' \
      '# OpenAI API (ChatGPT Plus does not provide this API quota)'
    printf 'OPENAI_API_KEY=%s\n' "${OPENAI_API_KEY}"
    printf '%s\n' \
      'EMBEDDING_MODEL=text-embedding-3-small' \
      'KNOWLEDGE_AGENT_MODEL=gpt-6-luna' \
      'AGENT_MODEL=gpt-6-luna' \
      'AGENT_REASONING_EFFORT=low' \
      '' \
      '# Knowledge / MCP Service'
    printf 'KNOWLEDGE_PUBLIC_BASE_URL=http://host.docker.internal:%s\n' \
      "${KNOWLEDGE_PORT}"
    printf '%s\n' 'KNOWLEDGE_DATA_DIR=/app/data'
    printf 'MCP_BEARER_TOKEN=%s\n' "${MCP_BEARER_TOKEN}"
    printf '%s\n' \
      'BROWSER_EXECUTOR_BASE_URL=http://browser-executor-service:8000'
    printf 'BROWSER_API_TOKEN=%s\n' "${BROWSER_API_TOKEN}"
    printf '%s\n' \
      'BROWSER_REQUEST_TIMEOUT_SECONDS=180' \
      '' \
      '# Browser Executor Service'
    printf 'BROWSER_PUBLIC_BASE_URL=http://host.docker.internal:%s\n' \
      "${BROWSER_PORT}"
    printf '%s\n' \
      'BROWSER_DATA_DIR=/app/data' \
      'MAX_PAGES=5' \
      'MAX_DAILY_BROWSER_RUNS=20' \
      'MAX_CONCURRENT_BROWSER_RUNS=1'
  } >"${ENV_FILE}"
  chmod 600 "${ENV_FILE}"

  printf '\n[OK] 已建立 Local .env；兩個服務 Token 已自動隨機產生。\n'
}

write_cloud_demo_env() {
  prompt_url \
    KNOWLEDGE_PUBLIC_BASE_URL \
    '已部署 Knowledge Service 的公開 HTTPS Base URL；Demo Agent 會在其後使用 /mcp。'
  prompt_url \
    BROWSER_PUBLIC_BASE_URL \
    '已部署 Browser Service 的公開 HTTPS Base URL；Direct Browser Demo 會呼叫 /browser-tasks。'
  prompt_secret \
    OPENAI_API_KEY \
    '本機展示 Client 使用的 OpenAI API Key；不會傳給 Zeabur 設定；為避免 Secret 外洩，輸入內容不會顯示於終端。'
  prompt_secret \
    MCP_BEARER_TOKEN \
    '必須與已部署 Knowledge Service 實際使用的 MCP Token 相同；為避免 Secret 外洩，輸入內容不會顯示於終端。'
  prompt_secret \
    BROWSER_API_TOKEN \
    '必須與已部署 Browser Service 實際使用的 Browser Token 相同；為避免 Secret 外洩，輸入內容不會顯示於終端。'

  umask 077
  {
    printf '%s\n' '# Local Demo Client configuration for deployed Cloud Services'
    printf 'OPENAI_API_KEY=%s\n' "${OPENAI_API_KEY}"
    printf 'KNOWLEDGE_PUBLIC_BASE_URL=%s\n' "${KNOWLEDGE_PUBLIC_BASE_URL}"
    printf 'MCP_BEARER_TOKEN=%s\n' "${MCP_BEARER_TOKEN}"
    printf 'BROWSER_PUBLIC_BASE_URL=%s\n' "${BROWSER_PUBLIC_BASE_URL}"
    printf 'BROWSER_API_TOKEN=%s\n' "${BROWSER_API_TOKEN}"
  } >"${ENV_FILE}"
  chmod 600 "${ENV_FILE}"

  printf '\n[DONE] 已建立只供本機 demo/ 驗證 Cloud Service 使用的 .env。\n'
  print_demo_next_step
}

require_local_runtime() {
  [[ "$(uname -s)" == "Linux" ]] \
    || fail 'Local 模式只支援 Ubuntu 24.04 Linux。'
  [[ -r /etc/os-release ]] || fail '無法確認 Linux 發行版本。'
  # shellcheck disable=SC1091
  source /etc/os-release
  [[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "24.04" ]] \
    || fail "需要 Ubuntu 24.04；目前為 ${PRETTY_NAME:-未知環境}。"
  command -v docker >/dev/null 2>&1 \
    || fail '找不到 docker；本腳本不會安裝主機工具。'
  docker compose version >/dev/null 2>&1 \
    || fail 'Docker Compose Plugin 不可用；本腳本不會自動安裝。'
  docker info >/dev/null 2>&1 \
    || fail 'Docker Daemon 不可用，或目前使用者沒有 Docker 權限。'
}

wait_for_health() {
  local service="$1"
  local attempt
  for attempt in {1..60}; do
    if docker compose exec -T "${service}" \
      python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" \
      >/dev/null 2>&1
    then
      printf '[OK] %s Health 可用。\n' "${service}"
      return 0
    fi
    sleep 2
  done
  fail "${service} 在等待時間內未通過 Health；請執行 docker compose logs ${service}。"
}

setup_local_runtime() {
  local fixtures=(
    test-data/meetings/base/weekly-meeting-01.pdf
    test-data/meetings/base/weekly-report-01.pptx
    test-data/meetings/base/weekly-meeting-02.pdf
    test-data/meetings/base/weekly-report-02.pptx
  )
  local fixture
  for fixture in "${fixtures[@]}"; do
    [[ -s "${fixture}" ]] || fail "固定 Test Data 不存在或為空：${fixture}"
  done

  printf '%s\n' \
    '[LOCAL SETUP] 將建立兩個 Service，並匯入 4 份模擬且混亂的企業文件：' \
    '  - test-data/meetings/base/weekly-meeting-01.pdf（第一週會議紀錄）' \
    '  - test-data/meetings/base/weekly-report-01.pptx（第一週主管週報）' \
    '  - test-data/meetings/base/weekly-meeting-02.pdf（第二週會議紀錄）' \
    '  - test-data/meetings/base/weekly-report-02.pptx（第二週主管週報）'

  printf '\n[1/4] 驗證 Docker Compose 設定。\n'
  docker compose config --quiet
  printf '\n[2/4] 建立 Knowledge 與 Browser Service Images。\n'
  docker compose build knowledge-mcp-service browser-executor-service
  printf '\n[3/4] 啟動兩個 Service 並等待 Health。\n'
  docker compose up -d knowledge-mcp-service browser-executor-service
  wait_for_health knowledge-mcp-service
  wait_for_health browser-executor-service
  printf '\n[4/4] 匯入四份固定企業文件。\n'
  docker compose exec -T knowledge-mcp-service \
    python scripts/ingest_meetings.py \
    --input test-data/meetings/base \
    >/dev/null
  printf '[OK] 四份固定企業文件已完成匯入。\n'

  printf '%s\n' \
    '' \
    '[DONE] 本機雙服務與 Knowledge Base 已可使用。' \
    "[KNOWLEDGE] http://localhost:${KNOWLEDGE_PORT}" \
    "[MCP] http://localhost:${KNOWLEDGE_PORT}/mcp" \
    "[BROWSER] http://localhost:${BROWSER_PORT}" \
    '[TEST DATA] test-data/meetings/base/（4 份 PDF／PPTX）' \
    '[NOTE] 重複執行會沿用既有 Volume；相同文件不新增第二份來源。'
  print_demo_next_step
}

print_demo_next_step() {
  printf '%s\n' \
    '' \
    '[NEXT] 建立並執行展示 Client：' \
    '  docker build -f demo/Dockerfile -t ai-home-test-demo .' \
    '  docker run --rm --env-file .env --add-host host.docker.internal:host-gateway --user "$(id -u):$(id -g)" -v "$PWD/logs/demo:/app/logs/demo" ai-home-test-demo'
}

if [[ "$#" -ne 0 ]]; then
  fail '此腳本不接受參數；請依互動提示選擇驗證目標。'
fi

printf '%s\n' \
  '[SETUP] AI Application Engineer Home Test' \
  '  y：使用本機 Demo Client 驗證已部署的 Cloud Services。' \
  '  n：建立 Ubuntu 24.04 本機雙服務並匯入固定文件。'

use_cloud="$(prompt_yes_no '是否使用已部署的 Cloud Service 驗證？[y/n] ')"
write_env="y"
if [[ -e "${ENV_FILE}" ]]; then
  printf '%s\n' \
    '[NOTICE] .env 已存在。' \
    '[NOTICE] 回答 n 會保留現有 .env，並繼續後續操作。'
  write_env="$(prompt_yes_no '是否覆寫？[y/n] ')"
  if [[ "${write_env}" == "n" ]]; then
    printf '[OK] 保留現有 .env，繼續後續操作。\n'
  fi
fi

if [[ "${use_cloud}" == "y" ]]; then
  if [[ "${write_env}" == "y" ]]; then
    write_cloud_demo_env
  else
    printf '[DONE] 已沿用現有 .env，供本機 demo/ 驗證 Cloud Service。\n'
    print_demo_next_step
  fi
else
  require_local_runtime
  if [[ "${write_env}" == "y" ]]; then
    write_local_env
  else
    load_existing_local_ports
  fi
  setup_local_runtime
fi
