# Тренажёр оператора 112 — частые команды
SHELL := /bin/bash

.PHONY: test-v2 config-check config-reload help up up-speech down logs ps health test smoke seed reset psql prewarm pdf ports tunnel open close status

help:
	@echo "  make test-v2     — приёмочный тест (50 проверок, без docker)"
	@echo "  make config-check — проверить config/ до запуска"
	@echo "  make config-reload — перечитать YAML без перезапуска"
	@echo "  make ports       — проверить, свободны ли порты"
	@echo "  make tunnel      — доступ с другого ПК через SSH (микрофон работает)"
	@echo "  make open        — открыть порты в локальную сеть (микрофон НЕ работает)"
	@echo "  make close       — вернуть порты на 127.0.0.1"
	@echo "  make status      — что сейчас слушает и по какому адресу открывать"
	@echo "  make up          — поднять текстовый режим (postgres, api, web)"
	@echo "  make up-speech   — то же + распознавание и синтез речи"
	@echo "  make down        — остановить"
	@echo "  make reset       — остановить и УДАЛИТЬ данные БД"
	@echo "  make health      — проверить состояние всех сервисов"
	@echo "  make test        — офлайн-тесты логики (без Docker)"
	@echo "  make smoke       — сквозной прогон вызова через API"
	@echo "  make seed        — наполнить аналитику синтетическими вызовами"
	@echo "  make prewarm     — предсинтез голосового банка (нужен профиль speech)"
	@echo "  make pdf         — скачать протокол последнего вызова"
	@echo "  make logs        — логи всех сервисов"
	@echo "  make psql        — консоль Postgres"

API_PORT ?= $(shell grep -E '^API_PORT=' .env 2>/dev/null | cut -d= -f2)
API_PORT := $(or $(API_PORT),21121)
API := http://localhost:$(API_PORT)

ports:
	@bash scripts/check_ports.sh

test-v2:
	@python3 scripts/test_v2.py

config-check:
	@python3 scripts/check_config.py

config-reload:
	@curl -s -X POST http://localhost:$(API_PORT)/api/v2/config/reload | python3 -m json.tool

open:
	@bash scripts/expose.sh open

close:
	@bash scripts/expose.sh close

status:
	@BIND=$$(grep -E '^BIND_HOST=' .env 2>/dev/null | cut -d= -f2); BIND=$${BIND:-127.0.0.1}; \
	WEB=$$(grep -E '^WEB_PORT=' .env 2>/dev/null | cut -d= -f2); WEB=$${WEB:-21120}; \
	URL=$$(grep -E '^NEXT_PUBLIC_API_URL=' .env 2>/dev/null | cut -d= -f2-); \
	echo; \
	echo "  BIND_HOST            $$BIND"; \
	echo "  NEXT_PUBLIC_API_URL  $$URL"; \
	echo; \
	if [ "$$BIND" = "0.0.0.0" ]; then \
	  IP=$$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($$i=="src") print $$(i+1)}' | head -1); \
	  echo "  Режим: открыт в сеть. Микрофон НЕ работает."; \
	  echo "  Открывать:  http://$${IP:-АДРЕС}:$$WEB"; \
	else \
	  echo "  Режим: только localhost. Микрофон работает через туннель."; \
	  echo "  С сервера:  http://localhost:$$WEB"; \
	  echo "  С чужого ПК: make tunnel"; \
	fi; \
	echo; \
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true

tunnel:
	@WEB=$$(grep -E '^WEB_PORT=' .env 2>/dev/null | cut -d= -f2); WEB=$${WEB:-21120}; \
	API=$$(grep -E '^API_PORT=' .env 2>/dev/null | cut -d= -f2); API=$${API:-21121}; \
	IP=$$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($$i=="src") print $$(i+1)}' | head -1); \
	[ -z "$$IP" ] && IP=$$(hostname -I 2>/dev/null | awk '{print $$1}'); \
	echo ""; \
	echo "Выполните ЭТУ команду на своём компьютере (не на сервере):"; \
	echo ""; \
	echo "    ssh -N -L $$WEB:localhost:$$WEB -L $$API:localhost:$$API $${SUDO_USER:-$${USER:-ВАШ_ЛОГИН}}@$${IP:-АДРЕС_СЕРВЕРА}"; \
	echo ""; \
	if [ "$${SUDO_USER:-$$USER}" = "root" ]; then \
	  echo "  Логин подставлен root. Во многих системах sshd запрещает вход"; \
	  echo "  root по паролю (PermitRootLogin prohibit-password) — тогда нужен"; \
	  echo "  ключ либо обычный пользователь вместо root."; \
	fi; \
	echo ""; \
	echo "Затем откройте в браузере:  http://localhost:$$WEB"; \
	echo ""; \
	echo "Так работает микрофон: браузер считает localhost доверенным адресом."; \
	echo "Ничего в .env менять не нужно."; \
	echo ""

up:
	@bash scripts/check_ports.sh || (echo; echo "Поправьте порты в .env и повторите."; exit 1)
	docker compose up -d --build postgres api web
	@echo "Ждём готовности API…"
	@for i in $$(seq 1 40); do \
	  curl -fsS $(API)/health >/dev/null 2>&1 && break || sleep 2; \
	done
	@$(MAKE) --no-print-directory health

up-speech:
	docker compose --profile speech up -d --build
	@echo "Первый старт ASR долгий: качаются веса GigaAM. Смотрите: make logs"

down:
	docker compose --profile speech down

reset:
	docker compose --profile speech down -v
	@echo "Тома удалены. Следующий 'make up' создаст БД заново и накатит db/*.sql"

logs:
	docker compose logs -f --tail=80

ps:
	docker compose ps

health:
	@curl -fsS $(API)/health | python3 -m json.tool || \
	  echo "API не отвечает на $(API). Смотрите: docker compose logs api"

test:
	python3 scripts/test_intents.py && python3 scripts/test_logic.py

smoke:
	python3 scripts/smoke_call.py

seed:
	python3 scripts/seed_analytics.py

prewarm:
	python3 scripts/prewarm_voicebank.py

pdf:
	@SID=$$(curl -fsS $(API)/api/analytics | \
	  python3 -c "import sys,json; r=json.load(sys.stdin)['rows']; print(r[0]['session_id'] if r else '')"); \
	if [ -z "$$SID" ]; then echo "Нет завершённых вызовов. Сначала: make smoke"; exit 1; fi; \
	curl -fsS -o protokol.pdf "$(API)/api/sessions/$$SID/report.pdf" && \
	echo "Сохранено: protokol.pdf (сессия $$SID)"

psql:
	docker compose exec postgres psql -U $${PG_USER:-t112} -d $${PG_DB:-trainer112}
