SHELL := /bin/bash
-include .env
PORT ?= 8080
IP = $$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($$i=="src") print $$(i+1)}' | head -1)
DC = docker compose --profile gpu --profile llm

.PHONY: help up up-cpu up-llm down restart logs ps health test tunnel open close passwords backup save gpu-check

help:
	@echo ""
	@echo "  make up         — ВСЁ: БД, приложение, голос, GigaAM на GPU   (рекомендуется)"
	@echo "  make up-cpu     — без видеокарты: распознаёт Vosk"
	@echo "  make up-llm     — как up + модель в контейнере vLLM (если её нет на хосте)"
	@echo "  make gpu-check  — видит ли Docker видеокарты"
	@echo "  make health     — состояние всех компонентов"
	@echo "  make passwords  — учётные записи первого запуска"
	@echo "  make tunnel     — команда для входа с вашего ПК"
	@echo "  make open/close — открыть/закрыть порт в сеть"
	@echo "  make backup     — резервная копия базы"
	@echo "  make save       — упаковать образы для машины без интернета"
	@echo "  make test       — приёмочные тесты без docker"
	@echo "  make logs / ps / restart / down"
	@echo ""

_prep:
	@[ -f .env ] || cp .env.example .env
	@mkdir -p state/pg state/app state/hf

up: _prep gpu-check
	docker compose --profile gpu up -d --build
	@echo "Первый запуск: сборка образов и загрузка моделей — 10–20 минут."
	@$(MAKE) --no-print-directory _wait

up-cpu: _prep
	ASR_ENGINE=vosk docker compose up -d --build
	@$(MAKE) --no-print-directory _wait

up-llm: _prep gpu-check
	LLM_BASE_URL=http://llm:8000/v1 docker compose --profile gpu --profile llm up -d --build
	@echo "Модель скачивается и загружается в видеопамять — до 20–40 минут в первый раз."
	@$(MAKE) --no-print-directory _wait

_wait:
	@echo "Ждём готовности…"; for i in $$(seq 1 180); do \
	  curl -fsS http://127.0.0.1:$(PORT)/health >/dev/null 2>&1 && break; sleep 2; done
	@$(MAKE) --no-print-directory health passwords tunnel

gpu-check:
	@command -v nvidia-smi >/dev/null || { echo "  nvidia-smi не найден. Без GPU: make up-cpu"; exit 1; }
	@nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader | sed 's/^/  GPU /'
	@docker run --rm --gpus all pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime nvidia-smi -L >/dev/null 2>&1 \
	  && echo "  Docker видит GPU: да" \
	  || { echo "  Docker НЕ видит GPU — NVIDIA Container Toolkit (INSTRUKCIYA.md, п. 8). Без GPU: make up-cpu"; exit 1; }

down:
	$(DC) down

restart:
	docker compose --profile gpu up -d --build --force-recreate

logs:
	$(DC) logs -f --tail=100

ps:
	$(DC) ps

health:
	@curl -fsS http://127.0.0.1:$(PORT)/health | python3 -c "import json,sys; d=json.load(sys.stdin); \
	print(''); print('  Приложение ..... работает'); \
	print('  База ........... ' + d.get('db','?')); \
	l=d['llm']; print('  Модель ......... ' + ('работает' if l.get('ok') else 'выключена' if l.get('ok') is None else 'НЕДОСТУПНА — ' + str(l.get('note') or l.get('error','')))); \
	v=d['voice']; g=v.get('gigaam') or {}; \
	print('  Распознавание .. ' + ('GigaAM на ' + str(g.get('device')) + ((' · ' + str(g.get('gpu'))) if g.get('gpu') else '') if v.get('engine')=='gigaam' else 'Vosk (CPU)' if v.get('engine')=='vosk' else 'не запущено')); \
	print('  Синтез речи .... ' + ('Piper' if v.get('tts') else 'не запущен')); \
	(v.get('note') and print('  Примечание ..... ' + v['note'])); \
	s=d['svodka']; print('  Данные ......... классификатор v%s (%s поз.), %s вызовов, %s служб' % (s['klassifikator']['versiya'],s['klassifikator']['poziciy'],s['bilety']['vsego'],s.get('sluzhb'))); print('')" \
	|| (echo "  Приложение не отвечает: make logs"; exit 1)

passwords:
	@docker logs t112 2>&1 | sed -n '/ПЕРВЫЙ ЗАПУСК/,/====/p' | head -9 || true

tunnel:
	@echo "  ─────────────────────────────────────────────────────────────"
	@echo "  На СВОЁМ компьютере:"
	@echo "    ssh -i ПУТЬ_К_КЛЮЧУ -N -L $(PORT):localhost:$(PORT) $${SUDO_USER:-$$USER}@$(IP)"
	@echo "  и в браузере:  http://localhost:$(PORT)"
	@echo "  ─────────────────────────────────────────────────────────────"

open:
	@sed -i 's/^BIND_HOST=.*/BIND_HOST=0.0.0.0/' .env
	docker compose up -d --force-recreate app
	@echo "  Открыто: http://$(IP):$(PORT)  (микрофон по http с чужого адреса не работает)"

close:
	@sed -i 's/^BIND_HOST=.*/BIND_HOST=127.0.0.1/' .env
	docker compose up -d --force-recreate app

backup:
	@docker exec t112 python3 -c "from api import db; print(db.backup_now())"

save:
	docker save postgres:16-alpine t112-app t112-voice $$(docker image inspect t112-asr >/dev/null 2>&1 && echo t112-asr) -o t112-images.tar
	@echo "  Готово: t112-images.tar"

test:
	@python3 scripts/test.py
	@echo ""; echo "  Маршрутизация речи:"; python3 scripts/test_voice.py 2>&1 | grep -E '^[0-9 ]'
