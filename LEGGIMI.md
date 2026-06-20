# Gestione Flusso — Aggiornamento unico (Rischio & Conformità + Analisi + KPI)

Pacchetto **completo** con tutte le modifiche di questa sessione. File interi: con
`git reset --hard` ottieni lo stato finale coerente in un solo aggiornamento.
**Testato end-to-end**: 7 suite di test (oltre 130 verifiche), check di sistema
pulito, nessuna regressione.

## Contenuto (in ordine cronologico)

### 1. Sicurezza — separazione dei compiti
La validazione del rischio è ora **strettamente legata al ruolo** (Legale→AI Act,
CISO→NIS2, DPO→GDPR): nessun bypass da superuser. La Funzione AI non può più
validare al posto dei presìdi (verificato: POST → 403).

### 2. Dashboard presìdi
CISO/DPO/Legale vedono in cima alla dashboard la card "Da validare —
<dimensione>" coi rischi della propria dimensione, linkati alla scheda.

### 3. Trattamento del rischio (ISO 27005)
Strategia **Accetta / Mitiga / Trasferisci / Evita**; per la mitigazione: azioni
con data prevista, livello **residuo** scelto dal presidio con **etichetta
automatica dell'operazione** ("Residuo BASSO = inerente MEDIO ridotto tramite N
azioni"), freccia di direzione e stato **da convalidare**. Residuo suggerito
indicativo (un livello sotto l'inerente). Rendering pulito degli obblighi (non più
lista grezza).

### 4. Analisi Funzione AI — nuovi campi
- **Unità del costo token**: Periodicità (mensile/annuale/una tantum) + Ambito
  (per utente/team/complessivo), come due dimensioni distinte.
- **Tipo di AI**: non agentica / agentica a supporto (human-in-the-loop) /
  agentica autonoma.
- **Infrastruttura**: API (cloud) / LLM locale (on-premise) / Ibrido.
- Tipo di AI e Infrastruttura sono **passati all'AI** quando classifica il rischio.

### 5. Costo annualizzato + numero utenti + totale
- **Costo token annualizzato** (mensile ×12, annuale ×1; una tantum non
  annualizzato), con anteprima **live** nel form.
- **Numero utenti/team** → **Costo token annuo TOTALE** (annualizzato × numero).
  Per ambiti per-unità senza il numero, il totale resta non determinabile (niente
  falsa precisione).

### 6. Stima AI dell'importo token
Se salvi l'analisi con l'importo token **vuoto** ma periodicità e ambito
impostati, l'AI **propone l'importo** al salvataggio (marcato "Stima AI ·
modificabile"). Tiene conto dell'infrastruttura: con **LLM locale** il costo token
API è ~0. Se inserisci l'importo a mano, il flag "stima AI" si azzera.

### 7. KPI aggiornati
Nuovi indicatori nel cruscotto e nella lettura esecutiva dell'AI:
- **Costo token annuo** del portafoglio (somma dei totali annualizzati).
- Mix **infrastruttura** (API/locale/ibrido) e **autonomia** (agentico/assistivo).
- **Governance del rischio**: rischi da validare, residui da convalidare, progetti
  pronti per la Direzione.

## File (12) + migrazioni (3)
`models.py, views.py, forms.py, urls.py, admin.py, ai_client.py, servizi.py,
kpi.py, templates/flusso/dettaglio.html, templates/flusso/dashboard.html,
templates/flusso/kpi.html, static/css/app.css` + migrazioni **0009, 0010, 0011**.

Le tre migrazioni aggiungono solo campi opzionali (blank/null/default): **sicure
sui dati esistenti**. `modifiche_complete.patch` è il diff unificato per review.

## Deploy (dopo commit/push sul branch)
Le migrazioni girano da sole (entrypoint.sh esegue `migrate`); in alternativa
`python manage.py migrate`.
```
cd /opt/Flusso-AI && sudo git fetch --depth 1 origin Ehocram-patch-1-completo && \
sudo git reset --hard origin/Ehocram-patch-1-completo && \
sudo docker compose up -d --build
```

## Note di metodo (CISO-to-CISO)
- **Rischio residuo** = giudizio del presidio + etichetta automatica dell'operazione,
  non un calcolo aritmetico (sarebbe falsa precisione).
- **Stima AI del costo** = punto di partenza human-in-the-loop, sempre modificabile,
  mai presentata come dato certo. Tiene conto di API vs locale.
- Il "Totale stimato" resta la **somma grezza** degli importi: non normalizzo
  altri costi a un orizzonte annuo (non hanno periodicità). Se vuoi un *costo annuo
  totale di progetto* (token annuo totale + altri costi annualizzati), aggiungo la
  periodicità anche ad "altri costi": è una riga in più, dimmi tu.

## Nota onesta sui test
Coprono logica, autorizzazioni, salvataggio, stima (con mock dell'AI), KPI e
presenza degli elementi HTML. **Non** coprono l'aspetto grafico nel browser né
l'anteprima live JS in esecuzione: aprendo la scheda, controlla il blocco costo
(4 colonne: importo + periodicità + ambito + numero utenti), l'anteprima live, il
blocco trattamento e i nuovi riquadri KPI. Se qualcosa stona nel CSS, lo sistemo.
