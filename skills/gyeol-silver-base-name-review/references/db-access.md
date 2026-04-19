# DB Access

## Default connection

- DSN: `postgresql://gyeol:gyeol_secret@localhost:5432/gyeol?host=/var/run/postgresql`

## Target table

- Table: `staging.product`
- Target column: `base_name`
- Scope: `platform = $platform AND platform_brand_id = $brand_id`
- Pending rows: `base_name IS NULL OR color IS NULL`
- Default ordering: `ORDER BY name`

## Batch query

```sql
SELECT id, name, category, url
FROM staging.product
WHERE platform = $platform
  AND platform_brand_id = $brand_id
  AND base_name IS NULL
ORDER BY category, name
LIMIT 50;
```

## Full scope query (for context)

Read ALL products in scope (including already-filled ones) to understand the naming pattern:

```sql
SELECT id, name, base_name
FROM staging.product
WHERE platform = $platform
  AND platform_brand_id = $brand_id
ORDER BY name;
```

## Count query

```sql
SELECT count(*)
FROM staging.product
WHERE platform = $platform
  AND platform_brand_id = $brand_id
  AND base_name IS NULL;
```

## Update pattern

```sql
WITH input(id, base_name, color) AS (VALUES
  ('uuid1'::uuid, 'BASE NAME 1', 'BLACK'),
  ('uuid2'::uuid, 'BASE NAME 1', 'NAVY'),
  ('uuid3'::uuid, 'BASE NAME 2', NULL)
)
UPDATE staging.product p
SET base_name = i.base_name,
    color = i.color
FROM input i
WHERE p.id = i.id;
```

## Rule

- Do not ask the user for DB connection info unless the default fails.
- After determining base_names, run the UPDATE directly.
- Only skip the write when the user explicitly requests read-only.
