#!/usr/bin/env bash
DB="postgresql://gyeol:gyeol_secret@localhost:5432/gyeol"

echo "=== Category Review ==="
for dir in /tmp/gyeol-silver-category-review/*/; do
  [ -d "$dir" ] || continue
  brand=$(basename "$dir")
  state=$(grep "^state=" "$dir/status.txt" 2>/dev/null | cut -d= -f2)
  batch=$(grep "^batch_no=" "$dir/status.txt" 2>/dev/null | cut -d= -f2)
  remaining=$(grep "^remaining_after=" "$dir/status.txt" 2>/dev/null | cut -d= -f2)
  printf "  %-30s %-10s batch=%-3s remaining=%s\n" "$brand" "$state" "$batch" "$remaining"
done

echo ""
echo "=== Brand Profile ==="
for dir in /tmp/gyeol-brand-profile/*/; do
  [ -d "$dir" ] || continue
  brand=$(basename "$dir")
  if [ -f "$dir/last_message.txt" ] && [ -s "$dir/last_message.txt" ]; then
    status="done"
  else
    status="running"
  fi
  printf "  %-30s %s\n" "$brand" "$status"
done

echo ""
echo "=== Training Data ==="
for dir in /tmp/gyeol-training-data/*/; do
  [ -d "$dir" ] || continue
  brand=$(basename "$dir")
  state=$(grep "^state=" "$dir/status.txt" 2>/dev/null | cut -d= -f2)
  style=$(grep "^style=" "$dir/status.txt" 2>/dev/null | cut -d= -f2)
  finished=$(grep "^finished_at=" "$dir/status.txt" 2>/dev/null | cut -d= -f2)
  printf "  %-30s %-10s style=%-14s finished=%s\n" "$brand" "${state:-unknown}" "${style:-}" "${finished:-}"
done

echo ""
echo "=== DB ==="
psql "$DB" -At -c "
SELECT 'products: ' || count(*) || ' (category NULL: ' || count(*) FILTER (WHERE category IS NULL) || ', woman: ' || count(*) FILTER (WHERE gender = 'woman') || ')'
FROM staging.product;"
psql "$DB" -At -c "
SELECT 'brands: ' || count(*) || ' (desc filled: ' || count(*) FILTER (WHERE description IS NOT NULL AND length(description) > 100) || ', style filled: ' || count(*) FILTER (WHERE style != '{}') || ')'
FROM staging.brand;"
