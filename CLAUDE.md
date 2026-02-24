# Claude Code - Notatki projektowe

## Deploy na serwer (192.168.0.169)

Serwer ma ten sam projekt pod `/home/luok/Projects/invest/`.
Kontener: `invest-orchestrator-1`

### Sposób 1: Kopiuj bezpośrednio do kontenera (szybki, bez rebuildu) ← PREFEROWANY
Ścieżki w kontenerze: `/app/src/` (nie `/app/orchestrator/src/`!)
```bash
# Rsync na serwer, potem docker cp do kontenera
rsync -av orchestrator/src/risk_manager.py 192.168.0.169:/home/luok/Projects/invest/orchestrator/src/
rsync -av config.yaml 192.168.0.169:/home/luok/Projects/invest/
ssh 192.168.0.169 "
  docker cp /home/luok/Projects/invest/orchestrator/src/risk_manager.py invest-orchestrator-1:/app/src/risk_manager.py
  docker cp /home/luok/Projects/invest/config.yaml invest-orchestrator-1:/app/data/config.yaml
  docker restart invest-orchestrator-1
"
```

Pliki źródłowe w kontenerze:
- `orchestrator/src/*.py` → `/app/src/*.py`
- `config.yaml` → `/app/data/config.yaml`

### Sposób 2: Rsync + rebuild (wolniejszy, zmiany trwałe)
```bash
rsync -av config.yaml orchestrator/src/risk_manager.py 192.168.0.169:/home/luok/Projects/invest/orchestrator/src/
rsync -av config.yaml 192.168.0.169:/home/luok/Projects/invest/
ssh 192.168.0.169 "cd /home/luok/Projects/invest && docker compose build --no-cache orchestrator && docker compose up -d orchestrator"
```

**Preferowany**: Sposób 1 (docker cp) - szybki, bez rebuildu. Po rebuildzie traci zmiany.

## Architektura

- **Orchestrator**: Docker na 192.168.0.169:8501 (Streamlit dashboard)
- **llama-swap**: 192.168.0.169:8080/v1 (LLM inference)
- **Ghostfolio**: 192.168.0.12:3333 (portfolio tracking)
- **Lokalne źródło**: `/home/luok/Projects/claude/investment/`
- **Zdalne źródło**: `192.168.0.169:/home/luok/Projects/invest/`

## Kluczowe pliki

- `config.yaml` - konfiguracja kont, strategii, risk profiles
- `orchestrator/src/risk_manager.py` - reguły ryzyka (min_holding, position limits, cost filter)
- `orchestrator/src/main.py` - scheduler + cykl decyzyjny
