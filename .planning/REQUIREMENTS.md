# Requirements: Telegram Freelance Lead Bot

**Defined:** 2026-04-30
**Core Value:** Быстро находить релевантные заказы на разработку раньше, чем они утонут в шуме фриланс-каналов.

## v1 Requirements

### Configuration

- [ ] **CONF-01**: User can provide Telegram `api_id`, `api_hash` and bot token through `.env`.
- [ ] **CONF-02**: User can edit monitored Telegram sources in code.
- [ ] **CONF-03**: User can edit include keywords, stop words and minimum score in code.

### Monitoring

- [ ] **MON-01**: App can connect to Telegram as a user session and resolve enabled sources.
- [ ] **MON-02**: App can process new messages from enabled sources.
- [ ] **MON-03**: App can catch up recent messages on startup.

### Filtering

- [ ] **FILT-01**: App rejects messages containing configured stop-words.
- [ ] **FILT-02**: App scores messages using weighted keywords.
- [ ] **FILT-03**: App accepts messages only when score reaches the configured threshold.

### Delivery

- [ ] **DELV-01**: Telegram bot can subscribe a chat via `/start`.
- [ ] **DELV-02**: Telegram bot sends accepted leads with source, link, date, matched terms, contacts and excerpt.
- [ ] **DELV-03**: Telegram bot exposes `/status`, `/sources`, `/keywords` and `/test` commands.

### Persistence

- [ ] **PERS-01**: App stores processed leads in SQLite.
- [ ] **PERS-02**: App deduplicates leads by source and Telegram message id.
- [ ] **PERS-03**: App stores subscriber chat ids locally.

### Documentation

- [ ] **DOCS-01**: README explains setup, first run auth, commands and filter tuning.
- [ ] **DOCS-02**: Example env file documents required and optional variables.
- [ ] **DOCS-03**: Secrets, sessions and local database are ignored by git.

## v2 Requirements

### Admin

- **ADMN-01**: User can add/remove sources via bot commands.
- **ADMN-02**: User can tune keywords via bot commands.
- **ADMN-03**: User can rate leads and generate improved filters.

### Ranking

- **RANK-01**: App can classify lead quality using historical feedback.
- **RANK-02**: App can detect budget and urgency more accurately.

## Out of Scope

| Feature | Reason |
|---------|--------|
| Auto-response to customers | Spam/blocking risk; user should respond manually |
| Browser scraping Telegram web pages | Less reliable than Telegram API |
| Web dashboard | Not needed for v1 |
| Cloud deployment | Local run is enough for first validation |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| CONF-01 | Phase 1 | Complete |
| CONF-02 | Phase 1 | Complete |
| CONF-03 | Phase 1 | Complete |
| MON-01 | Phase 2 | Complete |
| MON-02 | Phase 2 | Complete |
| MON-03 | Phase 2 | Complete |
| FILT-01 | Phase 1 | Complete |
| FILT-02 | Phase 1 | Complete |
| FILT-03 | Phase 1 | Complete |
| DELV-01 | Phase 2 | Complete |
| DELV-02 | Phase 2 | Complete |
| DELV-03 | Phase 2 | Complete |
| PERS-01 | Phase 1 | Complete |
| PERS-02 | Phase 1 | Complete |
| PERS-03 | Phase 2 | Complete |
| DOCS-01 | Phase 3 | Complete |
| DOCS-02 | Phase 3 | Complete |
| DOCS-03 | Phase 3 | Complete |

**Coverage:**
- v1 requirements: 18 total
- Mapped to phases: 18
- Unmapped: 0

---
*Requirements defined: 2026-04-30*
*Last updated: 2026-04-30 after initial implementation*

