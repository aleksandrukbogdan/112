#!/usr/bin/env bash
# Открыть или закрыть доступ к стенду из локальной сети.
#
#   bash scripts/expose.sh open    — слушать на всех интерфейсах
#   bash scripts/expose.sh close   — вернуть на 127.0.0.1
#
# Правит .env и пересоздаёт контейнеры. Пересоздаёт, а не перезапускает:
# NEXT_PUBLIC_API_URL читается процессом Next при старте, и обычный
# restart её не подхватит.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

REZHIM="${1:-}"
[ -f .env ] || { echo "Нет .env — скопируйте .env.example"; exit 1; }

get() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2 | tr -d ' \r'; }

set_env() {                                  # set_env ИМЯ ЗНАЧЕНИЕ
  if grep -qE "^$1=" .env; then
    sed -i -E "s|^$1=.*|$1=$2|" .env
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}

WEB=$(get WEB_PORT); WEB=${WEB:-21120}
API=$(get API_PORT); API=${API:-21121}

# Определяем адрес по маршруту наружу. hostname -I сюда не годится:
# первым он часто отдаёт docker0 или адрес другого моста.
opredelit_ip() {
  local ip
  ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
  [ -z "$ip" ] && ip=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -vE '^(127\.|172\.1[7-9]\.|172\.2[0-9]\.|172\.3[01]\.)' | head -1)
  echo "$ip"
}

case "$REZHIM" in

  open)
    IP=$(opredelit_ip)
    if [ -z "$IP" ]; then
      echo "Не удалось определить адрес сервера."
      echo "Задайте вручную:  NEXT_PUBLIC_API_URL=http://АДРЕС:$API"
      exit 1
    fi

    cp .env .env.bak
    set_env BIND_HOST "0.0.0.0"
    set_env NEXT_PUBLIC_API_URL "http://$IP:$API"

    echo "Пересоздаю контейнеры…"
    docker compose up -d --force-recreate web api postgres

    cat <<KONETS

Доступ открыт в сеть.

    Интерфейс   http://$IP:$WEB
    API         http://$IP:$API
    Документация http://$IP:$API/docs

МИКРОФОН В ЭТОМ РЕЖИМЕ НЕ РАБОТАЕТ.
Браузер даёт доступ к микрофону только в защищённом контексте —
это https или localhost. По http с чужого адреса записи не будет.

Нужен микрофон — закройте порты и используйте туннель:

    make close
    make tunnel

Порты открыты для всей сети. Если сервер доступен извне, закройте
их фаерволом или ограничьте подсеть:

    sudo ufw allow from 10.109.0.0/16 to any port $WEB
    sudo ufw allow from 10.109.0.0/16 to any port $API

Прежний .env сохранён как .env.bak
KONETS
    ;;

  close)
    cp .env .env.bak
    set_env BIND_HOST "127.0.0.1"
    set_env NEXT_PUBLIC_API_URL "http://localhost:$API"

    echo "Пересоздаю контейнеры…"
    docker compose up -d --force-recreate web api postgres

    cat <<KONETS

Доступ закрыт. Сервисы слушают только 127.0.0.1.

С самого сервера:  http://localhost:$WEB
С другого ПК:      make tunnel

Прежний .env сохранён как .env.bak
KONETS
    ;;

  *)
    BIND=$(get BIND_HOST); BIND=${BIND:-127.0.0.1}
    echo "Сейчас BIND_HOST=$BIND"
    echo
    echo "  bash scripts/expose.sh open    — открыть в сеть (без микрофона)"
    echo "  bash scripts/expose.sh close   — закрыть на localhost"
    exit 1
    ;;
esac
