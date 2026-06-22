# Gestione Flusso — Aggiornamento completo (workflow decisione budget + rifiuto/archiviazione + KPI)

Pacchetto **unico** con tutte le modifiche. File interi: con `git reset --hard`
ottieni lo stato finale coerente in un solo aggiornamento. **Testato end-to-end**:
9 suite (oltre 180 verifiche), check di sistema pulito, nessuna regressione.
Migrazioni **0009 → 0013** incluse (solo campi opzionali/choices → sicure sui dati esistenti).

## Il nuovo flusso di budget (novità principale di questa release)

Prima il budget si dichiarava all'intake. Ora è una **decisione dell'owner a valle
del costo**, più realistica: l'owner spesso non conosce il costo prima dell'analisi.

1. **Owner** crea la richiesta **senza budget/extra** — indica il **numero utenti**
   del software richiesto e tutto il resto.
2. **Funzione AI** prende in carico e fa l'analisi: scrive il **costo** (importo
   token con l'aiuto dell'AI, altri costi a mano).
3. **Funzione AI** → «Invia all'owner per la decisione di budget» (nuovo stato
   **Attesa decisione budget**). Gate: serve il costo di progetto calcolato.
4. **Owner** **deve obbligatoriamente** indicare se l'importo è **a budget** o
   **extra budget**. Alla conferma, **l'AI genera i rischi** (AI Act → Legale,
   NIS2 → CISO, GDPR → DPO) e il flusso prosegue **come prima** (validazione presìdi,
   presentazione alla Direzione, approvazione).
5. **In alternativa, l'owner rifiuta** il progetto con un **campo note per la
   motivazione**: la pratica passa allo stato **Archiviata** e non prosegue (nuova
   funzione). La motivazione resta nell'audit trail.

Spostamenti rispetto a prima:
- I **rischi non si generano più alla presa in carico**, ma **alla conferma del
  budget** dell'owner.
- **Presenta per l'approvazione** ora è bloccato finché l'owner non ha deciso il
  budget (oltre alle tre validazioni di rischio già richieste).

## KPI (solo progetti approvati)
- «A budget» ed «extra budget» del portafoglio ora derivano dalla **decisione
  dell'owner** (`esito_budget`): l'intero costo del progetto finisce nella voce
  scelta. Per i dati storici senza flag, ripiego automatico sulla ripartizione per
  importi (se l'owner aveva indicato un budget).
- I progetti **archiviati** sono esclusi dai KPI (come i respinti): contano solo
  gli approvati (APPROVATA/ATTIVO/MONITORAGGIO/COMPLETATO).

## Contenuto cumulativo del pacchetto
Oltre al workflow di budget, include tutto il lavoro recente: numero utenti +
costo annuo totale, stima AI dell'importo token, verifica a/extra budget, KPI
Direzione, e la rinomina etichetta «Saving economico» → «Beneficio economico atteso»
(solo etichette visibili; campo a DB invariato).

## File (15) + migrazioni (5)
`models.py, views.py, forms.py, urls.py, admin.py, ai_client.py, servizi.py,
kpi.py, workflow.py, templates/flusso/{dettaglio,dashboard,kpi,richiesta_form,
_scheda}.html, static/css/app.css` + migrazioni **0009–0013**.

## Deploy (dopo commit/push sul branch)
```
cd /opt/Flusso-AI && sudo git fetch --depth 1 origin Ehocram-patch-1-completo && \
sudo git reset --hard origin/Ehocram-patch-1-completo && \
sudo docker compose up -d --build
```
Le migrazioni girano da sole (entrypoint.sh esegue `migrate`).

## Nota onesta sui test
Le 9 suite coprono la macchina a stati (rischi generati post-budget, decisione
obbligatoria, autorizzazioni owner/AI officer, gate, rifiuto/archiviazione), i KPI
flag-based e la presenza degli elementi HTML del pannello. **Non** coprono la resa
grafica nel browser. Dopo il deploy verifica il pannello «decisione budget» lato
owner (radio a budget/extra budget + rifiuta con motivazione), e che il badge
«Decisione dell'owner» compaia nell'analisi. Se il CSS stona, lo sistemo.
