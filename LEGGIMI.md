# Gestione Flusso — correzioni Rischio & Conformità

Tre interventi sulla classificazione del rischio, **testati end-to-end** (check di
sistema, test unitari, rendering pagine, POST HTTP). Branch di riferimento:
`Ehocram-patch-1-completo`.

## 1. Sicurezza — separazione dei compiti (la più importante)
**Problema:** la Funzione AI poteva validare le dimensioni di competenza di
CISO/DPO/Legale.
**Causa:** il ruolo `AI_OFFICER` è forzato a `is_superuser=True` (requisito SSO) e
`_puo_validare()` lasciava passare *qualsiasi* superuser.
**Fix:** in `flusso/views.py`, `_puo_validare()` ora è **strettamente legato al
ruolo** (Legale→AI Act, CISO→NIS2, DPO→GDPR). Nessun bypass da superuser: né la
Funzione AI né altri superuser possono validare al posto del presidio.
Verificato anche via POST: la Funzione AI riceve **403**, il presidio competente
**302** e salva.

> Residuo da decidere (non modificato): la Funzione AI resta superuser, quindi
> mantiene accesso al *Django admin*, dove in teoria potrebbe editare le
> classificazioni. È un percorso separato e audit-trailato. Se vuoi chiudere
> anche quello, va rivisto il requisito "AI Officer = superuser" (impatti SSO):
> dimmelo e lo gestiamo.

## 2. UX — i presìdi vedono subito cosa validare
In `dashboard.html` + `views.py`: per CISO/DPO/Legale compare in cima alla
dashboard la card **"Da validare — <dimensione>"** con l'elenco dei rischi della
*propria* dimensione in stato "proposto dall'AI", linkati direttamente alla
scheda (ancora `#rischio`). La Funzione AI non la vede (non è un presidio).

## 3. Trattamento del rischio — più professionale
- **Rendering corretto:** gli "Obblighi e misure di trattamento" non sono più una
  lista Python grezza (`['...','...']`) ma un **elenco puntato pulito**. Un parser
  robusto (`obblighi_in_voci`) normalizza array JSON, repr di lista (dati storici)
  e testo a righe → **risolve anche le schede già salvate, senza migrazione**.
- **Prompt migliorati:** i tre prompt (AI Act/NIS2/GDPR) chiedono ora un *array
  JSON* di 3-6 misure concrete e attuabili, formulate in modo professionale.
- **Trattamento editabile dal presidio:** nel form di validazione c'è un campo
  "Obblighi e misure di trattamento" (una voce per riga), precompilato con le
  misure proposte dall'AI. Il presidio le rifinisce e le fa proprie. Campo vuoto =
  mantiene le misure esistenti.

## File modificati (7)
- `flusso/models.py` — mappa ruolo→dimensione, parser `obblighi_in_voci`,
  proprietà `obblighi_voci`, `valida()` salva il trattamento, prompt aggiornati
- `flusso/views.py` — fix `_puo_validare`, dashboard presìdi, form precompilato, salvataggio trattamento
- `flusso/ai_client.py` — normalizzazione obblighi (array→righe)
- `flusso/forms.py` — campo `obblighi` nel form di validazione
- `templates/flusso/dashboard.html` — card "Da validare"
- `templates/flusso/dettaglio.html` — elenco misure + campo trattamento + ancora `#rischio`
- `static/css/app.css` — stile elenco misure

Nessuna migrazione richiesta (nessun campo nuovo nel DB).

## Deploy in produzione (dopo il commit/push sul branch)
```
cd /opt/Flusso-AI && sudo git fetch --depth 1 origin Ehocram-patch-1-completo && \
sudo git reset --hard origin/Ehocram-patch-1-completo && \
sudo docker compose up -d --build
```
