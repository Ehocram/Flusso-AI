from django.urls import path

from . import views

app_name = "flusso"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("richieste/", views.lista, name="lista"),
    path("board/", views.kanban, name="kanban"),
    path("kpi/", views.kpi, name="kpi"),
    path("kpi/genera/", views.genera_analisi_kpi, name="genera_analisi_kpi"),
    path("kpi/impostazioni/", views.impostazioni_ai, name="impostazioni_ai"),
    path("kpi/impostazioni/prova/", views.prova_connessione_ai, name="prova_connessione_ai"),
    path("rischio/", views.rischio, name="rischio"),
    path("richieste/nuova/", views.nuova, name="nuova"),
    path("richieste/<int:pk>/", views.dettaglio, name="dettaglio"),
    path("richieste/<int:pk>/modifica/", views.modifica, name="modifica"),
    path("richieste/<int:pk>/elimina/", views.elimina, name="elimina"),
    path("richieste/<int:pk>/azione/", views.esegui_azione, name="esegui_azione"),
    path("schedulazione/", views.schedulazione, name="schedulazione"),
    path("effort/", views.ripartizione_effort, name="ripartizione_effort"),
    path("costi/", views.costi, name="costi"),
    path("budget/", views.budget_indice, name="budget"),
    path("budget/<slug:chiave>/", views.budget_foglio, name="budget_foglio"),
    path("budget/riga/<int:pk>/salva/", views.salva_riga_budget, name="salva_riga_budget"),
    path("budget/<slug:chiave>/riga/nuova/", views.nuova_riga_budget, name="nuova_riga_budget"),
    path("budget/<slug:chiave>/anno/", views.crea_foglio_anno, name="crea_foglio_anno"),
    path("budget-nuovo/", views.crea_foglio_vuoto, name="crea_foglio_vuoto"),
    path("richieste/<int:pk>/effort/ai/", views.genera_ripartizione, name="genera_ripartizione"),
    path("richieste/<int:pk>/effort/salva/", views.salva_ripartizione, name="salva_ripartizione"),
    path("richieste/<int:pk>/effort/griglia/", views.crea_griglia_effort_view, name="crea_griglia_effort"),
    path("richieste/<int:pk>/analisi/", views.aggiorna_analisi, name="aggiorna_analisi"),
    path("richieste/<int:pk>/analisi/ai/", views.compila_analisi_ai, name="compila_analisi_ai"),
    path("richieste/<int:pk>/beneficio/", views.aggiorna_beneficio, name="aggiorna_beneficio"),
    path("richieste/<int:pk>/pianificazione/", views.salva_pianificazione, name="salva_pianificazione"),
    path("richieste/<int:pk>/budget/", views.decidi_budget, name="decidi_budget"),
    path("richieste/<int:pk>/sal/", views.aggiorna_sal, name="aggiorna_sal"),
    path("richieste/<int:pk>/rischio/analizza/", views.analizza_rischio, name="analizza_rischio"),
    path("richieste/<int:pk>/rischio/<str:tipo>/valida/", views.valida_rischio, name="valida_rischio"),
    path("richieste/<int:pk>/rischio/<str:tipo>/tratta/", views.tratta_rischio, name="tratta_rischio"),
]
