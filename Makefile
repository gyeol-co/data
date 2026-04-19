DB_URL = postgres://gyeol:gyeol_secret@localhost:5432/gyeol?sslmode=disable
MIGRATIONS = migrations

.PHONY: migrate-up migrate-down

## 마이그레이션 적용 (make migrate-up / make migrate-up N=1 / make migrate-up N=3)
migrate-up:
	migrate -path $(MIGRATIONS) -database "$(DB_URL)" up $(N)

## 마이그레이션 롤백 (make migrate-down / make migrate-down N=1 / make migrate-down N=3)
migrate-down:
	migrate -path $(MIGRATIONS) -database "$(DB_URL)" down $(N)
