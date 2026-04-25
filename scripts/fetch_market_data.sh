#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RAW_DIR="$ROOT/data/raw"
EASTMONEY_DIR="$RAW_DIR/eastmoney"
TONGHUASHUN_DIR="$RAW_DIR/tonghuashun"
mkdir -p "$EASTMONEY_DIR" "$TONGHUASHUN_DIR" "$ROOT/output" "$ROOT/data"

UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
BASE_URL="https://push2.eastmoney.com/api/qt/clist/get?np=1&fltt=2&invt=2&ut=8dec03ba335b81bf4ebdf7b29ec27d15&pn=1&pz=500&po=1&fs=m:90+t:3"

fetch_file() {
  local url="$1"
  local target="$2"
  local tmp="${target}.tmp"
  shift 2

  if curl -L --retry 2 --retry-delay 1 --silent --show-error --fail "$@" "$url" -o "$tmp"; then
    mv "$tmp" "$target"
    echo "updated $(basename "$target")"
    return 0
  fi

  rm -f "$tmp"
  echo "warning: failed to update $(basename "$target"), keep old file if exists" >&2
  return 0
}

fetch_file \
  "${BASE_URL}&fid=f62&fields=f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124,f1,f13" \
  "$EASTMONEY_DIR/1d.json" \
  -A "$UA"

fetch_file \
  "${BASE_URL}&fid=f267&fields=f12,f14,f2,f127,f267,f268,f269,f270,f271,f272,f273,f274,f275,f276,f257,f258,f124,f1,f13" \
  "$EASTMONEY_DIR/3d.json" \
  -A "$UA"

fetch_file \
  "${BASE_URL}&fid=f164&fields=f12,f14,f2,f109,f164,f165,f166,f167,f168,f169,f170,f171,f172,f173,f257,f258,f124,f1,f13" \
  "$EASTMONEY_DIR/5d.json" \
  -A "$UA"

fetch_file \
  "${BASE_URL}&fid=f174&fields=f12,f14,f2,f160,f174,f175,f176,f177,f178,f179,f180,f181,f182,f183,f260,f261,f124,f1,f13" \
  "$EASTMONEY_DIR/10d.json" \
  -A "$UA"

fetch_file \
  "https://data.10jqka.com.cn/funds/gnzjl/field/tradezdf/order/desc/page/1/free/1/" \
  "$TONGHUASHUN_DIR/1d.html" \
  -A "$UA" \
  -H "Referer: https://data.10jqka.com.cn/funds/gnzjl/"

fetch_file \
  "https://data.10jqka.com.cn/funds/gnzjl/board/3/field/tradezdf/order/desc/page/1/free/1/" \
  "$TONGHUASHUN_DIR/3d.html" \
  -A "$UA" \
  -H "Referer: https://data.10jqka.com.cn/funds/gnzjl/"

fetch_file \
  "https://data.10jqka.com.cn/funds/gnzjl/board/5/field/tradezdf/order/desc/page/1/free/1/" \
  "$TONGHUASHUN_DIR/5d.html" \
  -A "$UA" \
  -H "Referer: https://data.10jqka.com.cn/funds/gnzjl/"

fetch_file \
  "https://data.10jqka.com.cn/funds/gnzjl/board/10/field/tradezdf/order/desc/page/1/free/1/" \
  "$TONGHUASHUN_DIR/10d.html" \
  -A "$UA" \
  -H "Referer: https://data.10jqka.com.cn/funds/gnzjl/"

fetch_file \
  "https://data.10jqka.com.cn/funds/gnzjl/board/20/field/tradezdf/order/desc/page/1/free/1/" \
  "$TONGHUASHUN_DIR/20d.html" \
  -A "$UA" \
  -H "Referer: https://data.10jqka.com.cn/funds/gnzjl/"

if ! python3 -m app.cli enrich-raw; then
  echo "warning: failed to enrich raw data, keep existing derived files if exists" >&2
fi

TZ=Asia/Shanghai date '+%F %T %Z' > "$RAW_DIR/last_fetch.txt"
echo "Fetched raw files into $RAW_DIR"
