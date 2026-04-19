---
name: gyeol-silver-base-name-review
description: Fill `staging.product.base_name` and `staging.product.color` for one `platform` + `platform_brand_id` by reading product names, identifying color variants, splitting the name into base product name and color, and writing both back to the DB.
---

# Gyeol Silver Base Name Review

Use this skill to fill missing `staging.product.base_name` and `staging.product.color` values for a single brand scope.

## What is base_name / color

`base_name` = product name without color/variant. Same product in different colors shares the same `base_name`.
`color` = the color/variant part extracted from the name.

Examples:
- `LOT 4601 PLAIN T BLACK` → base_name: `LOT 4601 PLAIN T`, color: `BLACK`
- `LOT 4601 PLAIN T NAVY` → base_name: `LOT 4601 PLAIN T`, color: `NAVY`
- `H.D. TRACK PANT - POLY SMOOTH CHARCOAL` → base_name: `H.D. TRACK PANT - POLY SMOOTH`, color: `CHARCOAL`
- `CINCHBACK TROUSERS BLACK` → base_name: `CINCHBACK TROUSERS`, color: `BLACK`
- `3WAY DOWN COAT NAVY` → base_name: `3WAY DOWN COAT`, color: `NAVY`
- `BK.Stone 80s High Waist Tapered Denim For Women` → base_name: `80s High Waist Tapered Denim For Women`, color: `BK.Stone`
- `DECONSTRUCTED LETTERED JACKET - WOOL MELTON` → 단독 상품: base_name = 전체 이름, color = NULL

## Defaults

- Scope is always one `platform` + one `platform_brand_id`.
- Work on rows where `base_name IS NULL` OR `color IS NULL`.
- Default batch size: `50`.
- Do not ask routine follow-up questions.
- Default behavior: read names, determine base_name + color, write UPDATE, report.

## Workflow

1. Query the batch using [references/db-access.md](references/db-access.md).
2. Read all product names in the scope. Look at the full list to understand the naming pattern for this brand.
3. Identify which products are color/variant versions of the same item:
   - Products that share everything except a color word at the end (or beginning for some brands).
   - Color words include but are not limited to: BLACK, NAVY, GREY, WHITE, CREAM, OLIVE, CHARCOAL, RED, BLUE, GREEN, BROWN, OATMEAL, etc.
   - Some colors are multi-word: OFF WHITE, PALE GREEN, FADED BLUE, DARK NAVY, etc.
   - Some brands use color names that aren't obvious: SMOKE, TOBACCO, LODEN, SAX, etc. Use context from the product group.
4. For grouped products: `base_name` = the shared name part, `color` = the differing part.
5. For singleton products (no color variant): `base_name` = the full product name, `color` = NULL.
6. Apply with a direct `psql` statement: `WITH input AS (VALUES ...) UPDATE ... SET base_name = ..., color = ...`.
7. Report updated row count.

## Decision Rules

- Trust the product name patterns. If 5 products start with `LOT 4601 PLAIN T` and end with different single words, those last words are colors.
- Preserve original casing. Do not uppercase or lowercase the base_name.
- When a product name has a dash separator before the color (e.g., `TRACK PANT - POLY SMOOTH CHARCOAL`), include everything up to and including the material/fabric part. The color is after the last material word.
- When uncertain whether a suffix is a color or part of the product name, check if other products share the prefix. If yes, it's likely a color. If the product is alone, keep the full name.
- Model numbers (e.g., `01-8270-84`) that appear in the name may or may not be part of the base_name. If the same model number appears with different color prefixes, strip the color.

## Resources

- Read [references/db-access.md](references/db-access.md) for connection defaults and query conventions.
- Use `scripts/run_until_done.sh` for unattended background execution.
- Use `scripts/status.sh` or `scripts/watch_status.sh` for monitoring.
