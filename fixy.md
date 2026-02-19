# Backlog poprawek — AI Investment Orchestrator

Znalezione podczas analizy logów z 2026-02-19. Posortowane wg priorytetu.

---

## 🔴 Krytyczne (przed prawdziwym kapitałem)

### 1. Halucynowane ceny stop-loss
**Problem:** LLM wpisuje ceny oderwane od rzeczywistości w polu `exit_condition` (wolny tekst).
Przykłady z logów:
- NVDA stop: `$1,020` / `$820` przy cenie **$187.90** (relikty sprzed split 10:1)
- JNJ stop: `$153` przy cenie **$246.91**
- SCHD stop: `$100` przy cenie **$31.57**
- CAT stop: `$280` przy cenie **$760.53**

**Fix:** Zastąpić wolne pole `exit_condition: string` strukturą:
```json
"stop_loss_pct": -15.0,
"take_profit_pct": 25.0,
"time_stop_days": 30
```
System sam przelicza na ceny. Eliminuje halucynacje.

Dodać też do system promptu: *"All price levels MUST be derived from current prices in MARKET DATA. Verify your computed prices are within a reasonable % of current price."*

---

### 2. Options: mylenie debit vs credit spread
**Problem:** LLM deklaruje "sprzedajemy premium" (theta positive), ale otwiera `BEAR_PUT`
(debit spread — PŁACIMY premium). Odwrotność zamierzonego.

**Fix:** Dodać do options Pass 2 system promptu:
```
CREDIT SPREADS (collect premium, theta positive — use when IV HIGH):
  BULL_PUT: sell higher put, buy lower put (bullish/neutral)
  BEAR_CALL: sell lower call, buy higher call (bearish/neutral)

DEBIT SPREADS (pay premium, directional — use when IV LOW):
  BULL_CALL: buy lower call, sell higher call (bullish)
  BEAR_PUT: buy higher put, sell lower put (bearish)

Rule: IV > 70th percentile → prefer CREDIT spreads.
      IV < 30th percentile → prefer DEBIT spreads.
```

---

## 🟡 Ważne

### 3. LLM ignoruje limit pozycji (20%)
**Problem:** `weekly_balanced` zaproponował VTI $5,000 = 50% portfela przy limicie 20%.
Risk manager uratował, ale LLM nie powinien tego robić.

**Fix:** Dodać do Pass 2 prompt:
*"Before proposing each trade compute: MAX_POSITION = total_value × max_position_pct / 100.
Your amount_usd MUST NOT exceed this. Show the calculation in reasoning."*

---

### 4. `portfolio_after` zawsze identyczne z `portfolio_before`
**Problem:** Snapshot po tradach jest pusty — dane zbierane przed potwierdzeniem zleceń.
Nie można zweryfikować efektu cyklu z audit logu.

**Fix:** W `audit_logger.log_cycle()` — odświeżyć portfolio state po wykonaniu tradów,
przed zapisem do JSON.

---

### 5. Brak RSI/SMA/MACD w danych options account
**Problem:** Options Pass 1 pokazuje `RSI:N/A Trend:?` dla wszystkich symboli.
LLM nie ma danych technicznych do analizy kierunkowej.

**Fix:** Dodać ten sam blok technical indicators (SMA20, SMA50, RSI, MACD histogram)
do options Pass 1 prompt — ten sam co equity accounts.

---

### 6. Monthly Value kupuje momentum zamiast value
**Problem:** CAT 18% powyżej SMA50, SCHD RSI=74 (overbought) — kupione bez analizy
fundamentalnej (P/E, yield, FCF), choć prompt tego wymaga.

**Fix opcja A:** Dodać do risk managera dla strategii `value_investing`:
warning gdy RSI > 70 lub brak danych fundamentalnych (pe_ratio=None).

**Fix opcja B:** Dodać do Pass 2 system promptu dla value_investing:
*"Only buy if you can cite at least one fundamental metric (P/E, P/B, dividend yield, or FCF yield).
RSI/MACD alone is NOT sufficient justification for a value trade."*

---

### 7. Brak korelacji/overlap check w risk managerze
**Problem:** `weekly_balanced` kupił jednocześnie VTI + VOO (oba = total market ETF, korelacja ~0.99).
Risk manager nie ostrzegł.

**Fix:** W risk managerze dodać listę znanych par wysoko-skorelowanych ETF:
`(VTI, VOO), (SPY, VOO), (QQQ, TQQQ), (SOXL, NVDA)` itp.
Jeśli dwa symbole z pary są w tym samym cyklu → warning.

---

## 🟢 Ulepszenia (nice to have)

### 8. Bootstrap mode — za mało tradów przy pustym portfelu
**Problem:** Limit 3-5 tradów/cykl uniemożliwia pełne zainwestowanie portfela w pierwszym cyklu.
`monthly_value` zainwestował tylko 45% zamiast 90%.

**Fix:** Jeśli `cash_pct > 80%` → podwoić `max_trades_per_cycle` na ten jeden cykl.

---

### 9. Dodać kalendarz earnings do promptu
**Problem:** LLM zgaduje terminy earnings z nagłówków newsów (niedokładnie).

**Fix:** Dodać blok do user promptu:
```
== UPCOMING EARNINGS (next 14 days) ==
NVDA: 2026-02-26 (in 7 days)
```
Źródło: yfinance `ticker.calendar`.

---

### 10. Filtrować news po watchliście konta
**Problem:** News o Jamesie Cameronie i Netflixie trafia do Daily Momentum i wpływa na reasoning.
LLM cytuje go jako "regulatory scrutiny threat".

**Fix:** Przed wysłaniem newsów do LLM — filtrować tylko te, które zawierają symbole
z watchlisty konta (lub przynajmniej nazwę sektora).

---

### 11. Dodać VIX do danych rynkowych
**Problem:** VIX nieobecny w danych dla equity accounts. Options account go używa,
equity nie — a powinny (sentiment/volatility indicator).

**Fix:** Dodać `^VIX` do `get_market_overview()` output w Pass 1 prompt dla wszystkich kont.

---

### 12. Wyniki poprzednich tradów w historii decyzji
**Problem:** Sekcja "PREVIOUS DECISIONS" pokazuje propozycje ale nie wyniki.
LLM nie wie czy poprzednie buye zyskały czy straciły.

**Fix:** W `format_decision_history()` — dołączyć aktualny P/L dla każdej poprzedniej pozycji:
```
[Week 1] BUY VTI $2000 → current P/L: +$84 (+4.2%)
```

---

### 13. Dodać datę do promptu (context dla LLM)
**Problem:** LLM nie wie jaki jest dzień tygodnia / bliskość weekendu / święta.

**Fix:** Dodać na początku user promptu:
```
== TODAY: Thursday 2026-02-19 ==
```

---

### 14. Ustrukturyzować sekcję sector analysis
**Problem:** `"sector_name": "OVERWEIGHT - reason"` — brak skali ilościowej.
LLM nie może wyrazić "bardzo overweight" vs "lekko overweight".

**Fix:** Dodać pole numeryczne:
```json
"Technology": {"rating": "UNDERWEIGHT", "score": -2, "reason": "..."}
```
Skala: -2 (strong underweight) do +2 (strong overweight).

---

*Ostatnia aktualizacja: 2026-02-19*
*Źródło: analiza logów daily_momentum_233025, monthly_value_232906, weekly_balanced_232740, options_spreads_233129*
