# ISEO AI · Gestione Flusso

Applicazione web per la gestione del **processo esigenze/opportunità AI** del
Gruppo ISEO (Op 4 — Intelligenza Artificiale). Gli **owner di funzione**
compilano la scheda progetto (il "rettangolo" del recap) e la richiesta
percorre il flusso di approvazione a 10 step fino all'avvio e al monitoraggio
del SAL, con **audit trail immutabile** di ogni passaggio.

Stack: **Django 5 + HTMX**, PostgreSQL (produzione) / SQLite (sviluppo),
reverse proxy **Caddy**, account locali (nessun SSO esterno).
Pensata per **deploy on-prem su rete interna**, non per esposizione su Internet.

---

## 1. Avvio rapido in locale (SQLite, ~60 secondi)

Requisiti: Python 3.12+.

```bash
python3 -m venv .venv && source .venv/bin/activate     
pip install -r requirements.txt

python manage.py migrate
python manage.py seed_demo          
python manage.py runserver
```
> Su macOS il venv va creato con `python3`; una volta attivato (`source .venv/bin/activate`)
> i comandi `python`/`pip` funzionano da soli.

Apri http://127.0.0.1:8000 e accedi con uno degli utenti demo
(password comune: `iseo2026`):

| Utente            | Ruolo                       |
|-------------------|-----------------------------|
| `owner.it` …      | Owner di funzione           |
| `marco.bonometti` | Funzione AI (AI Officer)    |
| `paolo.laini`     | Comitato AI                 |
| `marco.temporiti` | Comitato AI                 |
| `ceo`             | CEO                         |

Per l'accesso all'**admin** Django (`/admin/`, audit trail in sola lettura):

```bash
python manage.py createsuperuser
```

> Ambiente dimostrativo: cambia le password prima di qualunque uso reale.

### Dati: elenco progetti da Excel
I progetti vengono caricati dal file **`flusso/data/Elenco_Progetti_AI.xlsx`** (l'elenco
esigenze/opportunità del Gruppo). Lo stato nel flusso è derivato dalle colonne dell'Excel:

| Condizione nell'Excel            | Stato assegnato            |
|----------------------------------|----------------------------|
| Approvazione = *Approvato*, SAL>0 | In monitoraggio (SAL)      |
| Approvazione = *Approvato*        | Progetto attivo            |
| Colonna *Soluzione* valorizzata   | Presentata al Comitato     |
| altrimenti                        | Inviata alla Funzione AI   |

Il proponente di ogni richiesta è l'owner della funzione indicata in *Richiedente*.
Dopo un aggiornamento del file puoi reimportare senza toccare gli utenti:

```bash
python manage.py importa_progetti --file /percorso/nuovo_elenco.xlsx   # crea gli ID mancanti
python manage.py importa_progetti --reset                              # azzera e reimporta
```

---

## 2. Il flusso (Sintesi del processo)

```
Owner            Funzione AI                 Comitato AI            CEO
─────            ───────────                 ───────────            ───
Bozza ──invia──▶ Inviata ──prendi in carico─▶ In qualifica
                 In qualifica ──presenta───▶ Presentata ──discuti─▶ In discussione
                                                            ──presenta al CEO─▶ Presentata CEO ──approva─▶ Approvata
Approvata ──avvia progetto (Funzione AI)──▶ Attivo ──monitoraggio──▶ In monitoraggio (SAL) ──completa──▶ Completato
```

Diramazioni: *richiedi integrazione* (torna in bozza), *respingi* in qualifica
/ in Comitato / dal CEO (→ Respinta). Le azioni e i ruoli abilitati sono
definiti, in un unico punto, in `flusso/workflow.py`.

### Autorizzazioni (RBAC, applicate lato server)
- **Owner**: crea/modifica le proprie bozze e le invia; vede solo le proprie richieste.
- **Funzione AI**: qualifica, presenta al Comitato, avvia il progetto, aggiorna il SAL.
- **Comitato**: discussione e presentazione al CEO.
- **CEO**: decisione di approvazione.
- **Admin/superuser**: tutto + admin Django.

Ogni transizione passa da `workflow.puo_eseguire()` prima di essere applicata:
i pulsanti in interfaccia sono solo un riflesso del permesso, non la sua fonte.

---

## 3. Deploy on-prem (Docker / Podman Compose)

```bash
cp .env.example .env        # valorizza SECRET_KEY, password DB, hostname interno
# build + avvio (PostgreSQL + app + Caddy)
docker compose up -d --build         # oppure: podman-compose up -d --build
```

- **Reverse proxy**: `Caddyfile` espone l'app in HTTPS sull'hostname interno.
  Di default usa la CA interna di Caddy (`tls internal`); per un certificato
  della **PKI aziendale** sostituire con `tls /percorso/cert.pem /percorso/key.pem`.
- **Hostname**: impostare `DJANGO_ALLOWED_HOSTS` e `DJANGO_CSRF_TRUSTED_ORIGINS`
  in `.env` e l'omonimo record DNS interno verso il proxy.
- **Seed iniziale**: impostando `SEED_DEMO=1` in `.env` i dati demo vengono
  caricati al primo avvio (da disattivare in esercizio).
- **File statici**: serviti dall'app via *whitenoise*, raccolti in fase di build.
- **Backup**: `pg_dump` del volume `db_data` + snapshot del volume.

Sizing indicativo: 2–4 vCPU, 4–8 GB RAM, 1 VM Linux (Ubuntu/Rocky).

---

## 4. Autenticazione (account locali)

Applicazione interna, non pubblicata: l'accesso usa **account locali**, con
credenziali nel database PostgreSQL dell'app. Niente SSO esterno.

- **Auto-registrazione**: dalla pagina di login → *Registrati*. L'utente crea il
  proprio account (nome utente, nome/cognome, email, **funzione**, dipartimento,
  password) e viene creato come **Owner di funzione**. I ruoli privilegiati
  (Funzione AI, Comitato, CEO) restano assegnabili solo dall'admin.
- Le password sono soggette ai validatori di Django (lunghezza minima, non comuni,
  ecc.) e archiviate con hash; in chiaro non sono mai memorizzate.

## 5. Gestione utenti e password (da admin)

Dall'**admin** (`/admin/`):
- La **Funzione AI** (ruolo *AI Officer*) è **superuser**: accesso completo, può
  **creare, modificare ed eliminare** gli utenti e cambiare ruolo/funzione/dipartimento.
- **Reset password**: aprire l'utente → link di cambio password (imposti una password
  temporanea) e attivare **«Deve cambiare la password al prossimo accesso»**.
- **Forzare il cambio** in blocco: selezionare gli utenti nell'elenco → azione
  *«Forza il cambio password al prossimo accesso»*. Al login successivo l'utente è
  reindirizzato alla pagina di cambio password finché non la aggiorna.
- L'utente può cambiare la password da sé in qualsiasi momento (icona in alto a destra).
- Primo accesso su un ambiente nuovo: `python manage.py createsuperuser` (oppure
  `seed_demo`, che configura `marco.bonometti` come AI Officer/superuser).

### Analisi della Funzione AI (per progetto)
Nel dettaglio di ogni richiesta la Funzione AI compila l'**analisi**: fattibilità,
effort (ore), data inizio lavori, data prevista consegna, costi token AI e altri
costi (con totale stimato). Gli altri ruoli la vedono in sola lettura.

---

## 6. KPI e lettura esecutiva AI

Pagina **KPI** (menu in alto, visibile a Funzione AI, Approvatore e Auditor): cruscotto
direzionale del portafoglio. **I numeri sono calcolati in modo deterministico** dai dati
del processo e dall'audit trail (totali, pipeline per fase, progetti per area, SAL medio,
tasso di approvazione, lead time, effort, investimento stimato). L'AI non calcola KPI:
genera solo la **lettura esecutiva** (sintesi, colli di bottiglia, raccomandazioni).

Configurazione dalla pagina **Impostazioni AI** in-app (pulsante nella pagina KPI,
riservata alla Funzione AI), con **form grafico** e **Prova connessione**: attivazione,
**API key Anthropic**, **modello** (Sonnet/Opus/Haiku/Fable), lunghezza massima risposta,
istruzioni di sistema e se includere o meno i titoli dei progetti attivi nel prompt
(default: solo numeri aggregati). La chiave non viene mai mostrata in chiaro (campo
password); lasciandola vuota si mantiene quella salvata. In alternativa si può usare la
variabile d'ambiente `ANTHROPIC_API_KEY`, che ha la precedenza e non salva la chiave nel
DB. Gli stessi parametri restano modificabili anche da Django admin. La lettura si
(ri)genera dal pulsante nella pagina KPI (solo Funzione AI) e resta in cache.

> **Rete:** con questa funzione l'app effettua chiamate **HTTPS in uscita verso
> `api.anthropic.com`** (porta 443). Senza egress i KPI restano comunque visibili: solo la
> generazione AI fallisce, con messaggio esplicito. Al modello vengono inviati esclusivamente
> i KPI aggregati (e, se attivato, i titoli dei progetti attivi); nessun altro contenuto
> delle richieste lascia l'ambiente.

## 7. Struttura del progetto

```
config/         impostazioni, URL, WSGI/ASGI
accounts/       utente custom (ruolo/funzione/dipartimento), registrazione, cambio pw, admin
flusso/
  workflow.py   macchina a stati: stati, transizioni, ruoli, permessi
  models.py     Richiesta (la scheda) + Transizione (audit trail)
  views.py      dashboard, lista, board, dettaglio, azioni, SAL — con RBAC
  forms.py      form di intake della scheda
  templatetags/ filtri di presentazione (icone, colori, stepper)
  importer.py    parser dell'elenco Excel + assegnazione stato
  management/commands/  seed_demo.py, importa_progetti.py
  data/          Elenco_Progetti_AI.xlsx (sorgente progetti)
templates/      base + pagine + partial della scheda (replica del recap)
static/         CSS, HTMX e webfont Tabler vendorizzati (nessuna CDN)
Dockerfile · compose.yaml · Caddyfile · gunicorn.conf.py · .env.example
```

---

## Note di sicurezza
- Applicazione da trattare come asset dell'ISMS (rilevante NIS2): inventario,
  hardening, gestione accessi e log.
- L'**audit trail** (`flusso.Transizione`) è in sola lettura anche da admin e
  costituisce evidenza del processo (chi/cosa/quando).
- I log applicativi sono emessi su stdout (logger `flusso.audit`) per la
  raccolta da parte del SIEM.
- In produzione (`DJANGO_DEBUG=0`): cookie `Secure`, HSTS, `X-Frame-Options:
  DENY`, `nosniff` già attivi.
