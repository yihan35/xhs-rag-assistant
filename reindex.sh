#!/usr/bin/env bash
# reindex.sh
# 重建所有用户的向量索引（embedding 策略变更后使用）
#
# 操作步骤：
#   1. 清空 ChromaDB 向量库（data/chroma_db/）
#   2. 将 SQLite 中所有笔记的 indexed 标记重置为 0
#   3. 对每个用户重新跑 reindex_all()，重新向量化并写回 ChromaDB
#
# 用法：
#   ./reindex.sh            # 重建所有用户
#   ./reindex.sh <user_id>  # 只重建指定用户

set -eo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

DB_PATH="$ROOT_DIR/data/notes.db"
CHROMA_PATH="$ROOT_DIR/data/chroma_db"

# ── 颜色输出 ──────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}▶${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
error()   { echo -e "${RED}✗${NC}  $*" >&2; }
success() { echo -e "${GREEN}✓${NC}  $*"; }

# ── 前置检查 ──────────────────────────────────────────────────────

if [[ ! -f "$DB_PATH" ]]; then
  error "找不到数据库：$DB_PATH"
  exit 1
fi

if ! command -v python &>/dev/null; then
  error "找不到 python，请激活正确的 conda 环境后再运行"
  exit 1
fi

# ── 确定目标用户 ──────────────────────────────────────────────────

USER_IDS=()

if [[ $# -ge 1 ]]; then
  USER_IDS+=("$1")
  info "仅重建指定用户：${USER_IDS[0]}"
else
  while IFS= read -r uid; do
    [[ -n "$uid" ]] && USER_IDS+=("$uid")
  done < <(sqlite3 "$DB_PATH" \
    "SELECT DISTINCT user_id FROM notes WHERE is_collected=1 AND user_id != '' ORDER BY user_id;")

  if [[ ${#USER_IDS[@]} -eq 0 ]]; then
    warn "数据库中没有已收藏的笔记，无需重建"
    exit 0
  fi
  info "共发现 ${#USER_IDS[@]} 个用户：${USER_IDS[*]}"
fi

# ── 统计待重建数量 ────────────────────────────────────────────────

TOTAL=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM notes WHERE is_collected=1;")
info "待重建笔记总数：$TOTAL 条"
echo ""

# ── 确认操作 ──────────────────────────────────────────────────────

warn "此操作将清空 ChromaDB 并重新向量化所有笔记"
warn "ChromaDB 路径：$CHROMA_PATH"
warn "过程中 RAG 检索不可用，建议在服务停止后执行"
echo ""
read -r -p "确认继续？[y/N] " confirm
if [[ ! "$confirm" =~ ^[yY]$ ]]; then
  echo "已取消"
  exit 0
fi
echo ""

# ── Step 1：清空 ChromaDB ─────────────────────────────────────────

info "Step 1/3  清空 ChromaDB..."
if [[ -d "$CHROMA_PATH" ]]; then
  rm -rf "$CHROMA_PATH"
  success "已删除 $CHROMA_PATH"
else
  success "ChromaDB 目录不存在，跳过"
fi

# ── Step 2：重置 indexed 标记 ─────────────────────────────────────

info "Step 2/3  重置 SQLite indexed 标记..."
sqlite3 "$DB_PATH" "UPDATE notes SET indexed = 0 WHERE is_collected = 1;"
RESET_COUNT=$(sqlite3 "$DB_PATH" "SELECT changes();")
success "已重置 $RESET_COUNT 条记录的 indexed 标记"

# ── Step 3：逐用户重建向量索引 ───────────────────────────────────

info "Step 3/3  开始重新向量化..."
echo ""

OVERALL_SUCCESS=0
OVERALL_FAILED=0
START_TIME=$(date +%s)

for USER_ID in "${USER_IDS[@]}"; do
  NOTE_COUNT=$(sqlite3 "$DB_PATH" \
    "SELECT COUNT(*) FROM notes WHERE user_id='$USER_ID' AND is_collected=1 AND content != '';")
  echo -e "  用户 ${YELLOW}${USER_ID}${NC}（${NOTE_COUNT} 条笔记）"

  RESULT=$(python - <<PYEOF
import json, sys
sys.path.insert(0, '$ROOT_DIR')
from rag.indexer import reindex_all
result = reindex_all('$USER_ID')
print(json.dumps(result))
PYEOF
  )

  REINDEXED=$(echo "$RESULT" | python -c "import sys,json; d=json.load(sys.stdin); print(d['reindexed'])")
  FAILED=$(echo "$RESULT"    | python -c "import sys,json; d=json.load(sys.stdin); print(d['failed'])")

  OVERALL_SUCCESS=$((OVERALL_SUCCESS + REINDEXED))
  OVERALL_FAILED=$((OVERALL_FAILED + FAILED))

  if [[ "$FAILED" -eq 0 ]]; then
    success "    完成：$REINDEXED 条成功"
  else
    warn "    完成：$REINDEXED 条成功，$FAILED 条失败"
  fi
done

# ── 汇总 ──────────────────────────────────────────────────────────

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
success "重建完成！耗时 ${ELAPSED}s"
echo -e "  成功：${GREEN}${OVERALL_SUCCESS}${NC} 条"
if [[ "$OVERALL_FAILED" -gt 0 ]]; then
  echo -e "  失败：${RED}${OVERALL_FAILED}${NC} 条（检查 API Key 或网络后可重新运行）"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
