#!/usr/bin/env bash
DB="postgresql://gyeol:gyeol_secret@localhost:5432/gyeol"

for dir in /tmp/gyeol-silver-category-review/*/; do
  brand=$(basename "$dir")
  state=$(grep "^state=" "$dir/status.txt" 2>/dev/null | cut -d= -f2)
  batch=$(grep "^batch_no=" "$dir/status.txt" 2>/dev/null | cut -d= -f2)
  remaining=$(grep "^remaining_after=" "$dir/status.txt" 2>/dev/null | cut -d= -f2)
  printf "%-30s state=%-10s batch=%-3s remaining=%s\n" "$brand" "$state" "$batch" "$remaining"
done

echo "---"
psql "$DB" -At -c "SELECT 'remaining=' || count(*) FILTER (WHERE category IS NULL) || ' filled=' || count(*) FILTER (WHERE category IS NOT NULL) || ' total=' || count(*) FROM staging.product;"
