#!/usr/bin/env bash
# Проверка портов перед развёртыванием.
#   make ports        — или   bash scripts/check_ports.sh
#
# Порты берутся из .env, если он есть, иначе — дефолты.
# Блок 21120-21125 выбран намеренно: ниже эфемерного диапазона Linux
# (тот начинается с 32768) и не пересекается с привычными 3000/5432/6379/8080.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

get() {                                  # get ИМЯ ДЕФОЛТ
  local name="$1" def="$2" val=""
  [ -f .env ] && val=$(grep -E "^${name}=" .env 2>/dev/null | tail -1 | cut -d= -f2 | tr -d ' \r')
  echo "${val:-$def}"
}

NAMES=(WEB_PORT API_PORT PG_PORT REDIS_PORT ASR_PORT TTS_PORT)
LABELS=("веб-интерфейс" "API" "Postgres" "Redis" "ASR (профиль speech)" "TTS (профиль speech)")
DEFAULTS=(21120 21121 21122 21123 21124 21125)

VALUES=()
for i in "${!NAMES[@]}"; do
  VALUES+=("$(get "${NAMES[$i]}" "${DEFAULTS[$i]}")")
done

# Определяем, чем вообще можно проверять. Если ничем — это надо сказать вслух,
# иначе скрипт молча отрапортует «свободно» и подведёт на развёртывании.
PROBE=""
command -v python3 >/dev/null 2>&1 && PROBE="python3"
[ -z "$PROBE" ] && command -v ss >/dev/null 2>&1 && PROBE="ss"
[ -z "$PROBE" ] && command -v lsof >/dev/null 2>&1 && PROBE="lsof"

# Надёжная проверка: пытаемся занять порт сами. Если не выходит — он занят,
# независимо от того, кто и как его держит.
bind_busy() {
  python3 - "$1" <<'PYEOF' 2>/dev/null
import socket, sys
port = int(sys.argv[1])
for family, addr in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET, "127.0.0.1")):
    s = socket.socket(family, socket.SOCK_STREAM)
    try:
        s.bind((addr, port))
    except OSError:
        sys.exit(1)          # занят
    finally:
        s.close()
sys.exit(0)                  # свободен
PYEOF
}

occupant() {                             # occupant ПОРТ -> кто держит, или пусто
  local port="$1" who="" dock="" busy=1

  if command -v docker >/dev/null 2>&1; then
    dock=$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null \
           | grep -E "(^|[^0-9]):${port}->" | awk '{print $1}' | head -1)
  fi

  if command -v ss >/dev/null 2>&1; then
    who=$(ss -ltn 2>/dev/null | awk -v p=":${port}\$" '$4 ~ p {print $NF; exit}')
    ss -ltn 2>/dev/null | awk -v p=":${port}\$" '$4 ~ p {f=1} END {exit !f}' && busy=0
  elif command -v lsof >/dev/null 2>&1; then
    who=$(lsof -iTCP:"$port" -sTCP:LISTEN -Pn 2>/dev/null | awk 'NR==2 {print $1" pid="$2}')
    [ -n "$who" ] && busy=0
  fi

  # контрольная проверка попыткой связывания — ловит то, что не показали утилиты
  if [ "$busy" -ne 0 ] && command -v python3 >/dev/null 2>&1; then
    bind_busy "$port" || busy=0
  fi

  if [ "$busy" -eq 0 ]; then
    [ -n "$dock" ] && who="контейнер ${dock} ${who}"
    [ -z "$who" ] && who="(процесс не определён — попробуйте sudo)"
    echo "$who"
  else
    echo ""
  fi
}

free_port() {                            # ищет свободный, начиная с указанного
  local p="$1"
  while [ "$p" -lt 65000 ]; do
    [ -z "$(occupant "$p")" ] && { echo "$p"; return; }
    p=$((p + 1))
  done
  echo ""
}

if [ -z "$PROBE" ]; then
  echo "Нечем проверить порты: нет ни python3, ни ss, ни lsof."
  echo "Установите любой из них, иначе проверка не имеет смысла."
  exit 2
fi

echo "Проверяю порты…"
echo

busy=0
FIXES=()

for i in "${!NAMES[@]}"; do
  port="${VALUES[$i]}"
  who=$(occupant "$port")
  if [ -n "$who" ]; then
    busy=1
    printf '  ЗАНЯТ  %-6s %-24s %s\n' "$port" "${LABELS[$i]}" "$who"
    alt=$(free_port $((port + 100)))
    [ -n "$alt" ] && FIXES+=("${NAMES[$i]}=${alt}")
  else
    printf '  своб.  %-6s %s\n' "$port" "${LABELS[$i]}"
  fi
done

echo

if [ "$busy" -eq 0 ]; then
  echo "Все порты свободны. Можно разворачивать: make up"
  exit 0
fi

echo "Часть портов занята. Впишите в .env свободные значения:"
echo
for f in "${FIXES[@]}"; do
  echo "    $f"
done

for f in "${FIXES[@]}"; do
  case "$f" in
    API_PORT=*)
      echo "    NEXT_PUBLIC_API_URL=http://localhost:${f#API_PORT=}"
      echo
      echo "  ВНИМАНИЕ: браузер обращается к API напрямую, поэтому при смене"
      echo "  API_PORT обязательно меняется и NEXT_PUBLIC_API_URL."
      ;;
  esac
done

echo
echo "После правки повторите: make ports"
exit 1
