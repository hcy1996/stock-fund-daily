#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RAW_DIR="$ROOT/data/raw"
EASTMONEY_DIR="$RAW_DIR/eastmoney"
TONGHUASHUN_DIR="$RAW_DIR/tonghuashun"
STATUS_FILE="$RAW_DIR/fetch_status.tsv"
mkdir -p "$EASTMONEY_DIR" "$TONGHUASHUN_DIR" "$ROOT/output" "$ROOT/data"
: > "$STATUS_FILE"

UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
EASTMONEY_BASE_URLS=(
  "http://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=500&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fs=m:90+t:3+f:!50"
  "http://79.push2.eastmoney.com/api/qt/clist/get?pn=1&pz=500&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fs=m:90+t:3+f:!50"
  "http://17.push2.eastmoney.com/api/qt/clist/get?pn=1&pz=500&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fs=m:90+t:3+f:!50"
)

record_status() {
  local source="$1"
  local window_days="$2"
  local status="$3"
  printf '%s\t%s\t%s\n' "$source" "$window_days" "$status" >> "$STATUS_FILE"
}

fetch_file() {
  local url="$1"
  local target="$2"
  local source="$3"
  local window_days="$4"
  local tmp="${target}.tmp"
  shift 4

  if curl -L --retry 2 --retry-delay 1 --silent --show-error --fail "$@" "$url" -o "$tmp"; then
    mv "$tmp" "$target"
    record_status "$source" "$window_days" "success"
    echo "updated $(basename "$target")"
    return 0
  fi

  rm -f "$tmp"
  record_status "$source" "$window_days" "failed"
  echo "warning: failed to update $(basename "$target"), keep old file if exists" >&2
  return 0
}

fetch_eastmoney_file() {
  local query="$1"
  local target="$2"
  local window_days="$3"
  local base_url

  for base_url in "${EASTMONEY_BASE_URLS[@]}"; do
    local tmp="${target}.tmp"
    if curl -L --retry 2 --retry-delay 1 --silent --show-error --fail -A "$UA" "${base_url}${query}" -o "$tmp"; then
      mv "$tmp" "$target"
      record_status "eastmoney" "$window_days" "success"
      echo "updated $(basename "$target")"
      return 0
    fi
    rm -f "$tmp"
  done

  record_status "eastmoney" "$window_days" "failed"
  echo "warning: failed to update $(basename "$target"), keep old file if exists" >&2
  return 0
}

fetch_eastmoney_file \
  "&fid=f62&fields=f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124,f1,f13" \
  "$EASTMONEY_DIR/1d.json" \
  1

fetch_eastmoney_file \
  "&fid=f267&fields=f12,f14,f2,f127,f267,f268,f269,f270,f271,f272,f273,f274,f275,f276,f257,f258,f124,f1,f13" \
  "$EASTMONEY_DIR/3d.json" \
  3

fetch_eastmoney_file \
  "&fid=f164&fields=f12,f14,f2,f109,f164,f165,f166,f167,f168,f169,f170,f171,f172,f173,f257,f258,f124,f1,f13" \
  "$EASTMONEY_DIR/5d.json" \
  5

fetch_eastmoney_file \
  "&fid=f174&fields=f12,f14,f2,f160,f174,f175,f176,f177,f178,f179,f180,f181,f182,f183,f260,f261,f124,f1,f13" \
  "$EASTMONEY_DIR/10d.json" \
  10

fetch_file \
  "https://data.10jqka.com.cn/funds/gnzjl/field/tradezdf/order/desc/page/1/free/1/" \
  "$TONGHUASHUN_DIR/1d.html" \
  tonghuashun \
  1 \
  -A "$UA" \
  -H "Referer: https://data.10jqka.com.cn/funds/gnzjl/"

fetch_file \
  "https://data.10jqka.com.cn/funds/gnzjl/board/3/field/tradezdf/order/desc/page/1/free/1/" \
  "$TONGHUASHUN_DIR/3d.html" \
  tonghuashun \
  3 \
  -A "$UA" \
  -H "Referer: https://data.10jqka.com.cn/funds/gnzjl/"

fetch_file \
  "https://data.10jqka.com.cn/funds/gnzjl/board/5/field/tradezdf/order/desc/page/1/free/1/" \
  "$TONGHUASHUN_DIR/5d.html" \
  tonghuashun \
  5 \
  -A "$UA" \
  -H "Referer: https://data.10jqka.com.cn/funds/gnzjl/"

fetch_file \
  "https://data.10jqka.com.cn/funds/gnzjl/board/10/field/tradezdf/order/desc/page/1/free/1/" \
  "$TONGHUASHUN_DIR/10d.html" \
  tonghuashun \
  10 \
  -A "$UA" \
  -H "Referer: https://data.10jqka.com.cn/funds/gnzjl/"

fetch_file \
  "https://data.10jqka.com.cn/funds/gnzjl/board/20/field/tradezdf/order/desc/page/1/free/1/" \
  "$TONGHUASHUN_DIR/20d.html" \
  tonghuashun \
  20 \
  -A "$UA" \
  -H "Referer: https://data.10jqka.com.cn/funds/gnzjl/"

if ! python3 -m app.cli enrich-raw; then
  echo "warning: failed to enrich raw data, keep existing derived files if exists" >&2
fi

TZ=Asia/Shanghai date '+%F %T %Z' > "$RAW_DIR/last_fetch.txt"
echo "Fetched raw files into $RAW_DIR"
