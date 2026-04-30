# Roadmap: Telegram Freelance Lead Bot

## Proposed Roadmap

**3 phases** | **18 requirements mapped** | All v1 requirements covered

| # | Phase | Goal | Requirements | Success Criteria |
|---|-------|------|--------------|------------------|
| 1 | Filtering Core | Define sources, keywords, scoring and persistence | CONF-02, CONF-03, FILT-01, FILT-02, FILT-03, PERS-01, PERS-02 | 4 |
| 2 | Telegram Runtime | Connect Telegram user and bot clients, monitor and deliver leads | CONF-01, MON-01, MON-02, MON-03, DELV-01, DELV-02, DELV-03, PERS-03 | 5 |
| 3 | Operability | Make setup and local operation safe and understandable | DOCS-01, DOCS-02, DOCS-03 | 3 |

## Phase Details

### Phase 1: Filtering Core

Goal: turn Telegram posts into accepted/rejected lead decisions with local dedupe.

Success criteria:
1. Keywords and stop-words are editable in code.
2. Relevant bot/parser posts pass the filter in tests.
3. Obvious SMM/noise posts fail the filter in tests.
4. SQLite prevents duplicate lead notifications.

### Phase 2: Telegram Runtime

Goal: run one process that reads sources and delivers matched leads to the user's bot.

Success criteria:
1. User client starts with `api_id/api_hash`.
2. Bot client starts with bot token.
3. `/start` subscribes the user chat.
4. New and catch-up messages are processed.
5. Accepted leads are formatted with useful context and sent once.

### Phase 3: Operability

Goal: make the project easy to start, tune and keep safe.

Success criteria:
1. README covers setup and first run.
2. `.env.example` documents required variables.
3. `.gitignore` excludes secrets, sessions and local database.

## Next Up

Run local verification, then launch with real Telegram credentials:

```bash
python -m unittest
python -m py_compile freelancer_bot/*.py
python -m freelancer_bot
```

