# Gestione Flusso — Trattamento del rischio (ISO 27005)

Aggiunge un ciclo completo di **trattamento del rischio** sulla scheda Rischio &
Conformità. Branch: `Ehocram-patch-1-completo`. **Testato end-to-end** (check di
sistema, 4 suite di test: parser obblighi, autorizzazioni, POST validazione, POST
trattamento — tutte verdi, nessuna regressione).

> Questo pacchetto contiene il **modulo Rischio completo**: include sia il fix
> precedente (separazione dei compiti, dashboard presìdi, rendering obblighi) sia
> il nuovo trattamento. I file sono completi: applicandoli ottieni lo stato
> finale corretto anche se avevi già committato il fix precedente. **Unica
> novità sul DB: la migrazione 0009** (i nuovi campi di trattamento).

## Cosa fa il trattamento
Per ogni dimensione (AI Act / NIS2 / GDPR), il presidio competente può scegliere
una **strategia di trattamento** (ISO 27005 / ISO 31000):

- **Mitigare (ridurre):** elenco di **azioni** con **data prevista di
  applicazione** + **rischio residuo** atteso. Il sistema genera in automatico
  l'**etichetta che esplicita l'operazione** ("Rischio residuo BASSO = inerente
  MEDIO ridotto tramite N azioni di mitigazione"), la **freccia di direzione**
  (↓ ridotto / = invariato) e lo stato **da convalidare** finché il presidio non
  spunta "Convalida il rischio residuo".
- **Trasferire:** livello residuo trattenuto + **campo note** (a chi/come:
  assicurazione, contratto, fornitore).
- **Evitare (eliminare):** il caso d'uso non procede nella forma che genera il
  rischio → residuo eliminato.
- **Accettare:** è il default. **Se non si fa nulla, il rischio resta accettato**
  come proposto dall'AI (con campo note facoltativo per motivare l'accettazione).

## Scelta di metodo (importante, CISO-to-CISO)
Il rischio residuo **non è calcolato aritmeticamente**: "inerente − mitigazioni"
non è una formula, è un giudizio esperto. Calcolarlo come numero sarebbe falsa
precisione. Quindi: **il presidio sceglie** il livello residuo, e il sistema
**automatizza l'etichetta dell'operazione, la direzione e lo stato di convalida**.
In più, quando si mitiga, viene proposto un **residuo suggerito indicativo** (un
livello sotto l'inerente) che il presidio conferma o cambia. Hai l'automatismo
richiesto, senza inventare un valore.

Note di governance:
- Trattamento e convalida del residuo sono riservati al **presidio competente**
  (Legale→AI Act, CISO→NIS2, DPO→GDPR), coerente col fix di separazione dei
  compiti. Verificato via POST: Funzione AI e presidio non competente → **403**.
- Il gate "presentazione alla Direzione" **non** è stato bloccato sul residuo
  convalidato (rispetta il principio "se non faccio nulla, accetto"). Se vuoi
  renderlo vincolante, è una riga in più: dimmelo.

## File (9) + migrazione
- `flusso/models.py` — enum `StrategiaTrattamento`, campi di trattamento +
  modello `AzioneTrattamento`, proprietà del residuo (codice/label/direzione/
  etichetta operazione/stato), `registra_trattamento()`, scala ordinale livelli
- `flusso/forms.py` — `TrattamentoRischioForm` + formset azioni (`descrizione` + `data_prevista`)
- `flusso/views.py` — vista `tratta_rischio` (gated), form/formset nel dettaglio
- `flusso/urls.py` — rotta `…/rischio/<tipo>/tratta/`
- `flusso/admin.py` — inline azioni + campi trattamento in admin
- `flusso/ai_client.py` — (dal fix) normalizzazione obblighi
- `templates/flusso/dettaglio.html` — blocco visualizzazione + form trattamento + JS (toggle strategia, "aggiungi azione")
- `templates/flusso/dashboard.html` — (dal fix) card "Da validare" per i presìdi
- `static/css/app.css` — stili trattamento (`.trk*`) + (dal fix) elenco misure
- `flusso/migrations/0009_…py` — **nuovi campi DB** (tutti con default/null: sicura sui dati esistenti)

## Deploy (dopo commit/push sul branch)
La migrazione gira da sola: l'`entrypoint.sh` esegue `migrate` all'avvio del
container. In alternativa, manuale: `python manage.py migrate`.
```
cd /opt/Flusso-AI && sudo git fetch --depth 1 origin Ehocram-patch-1-completo && \
sudo git reset --hard origin/Ehocram-patch-1-completo && \
sudo docker compose up -d --build
```

## Nota onesta sui test
I test coprono logica del residuo, autorizzazioni, salvataggio formset (POST) e
presenza degli elementi HTML. **Non** ho potuto verificare l'aspetto grafico nel
browser: quando apri la scheda, controlla l'allineamento del blocco trattamento,
delle righe azione (formset) e della freccia di direzione. Se qualcosa stona nel
CSS, lo sistemo in un attimo.
