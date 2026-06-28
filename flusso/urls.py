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
    path("richieste/<int:pk>/analisi/", views.aggiorna_analisi, name="aggiorna_analisi"),
    path("richieste/<int:pk>/analisi/ai/", views.compila_analisi_ai, name="compila_analisi_ai"),
    path("richieste/<int:pk>/pianificazione/", views.salva_pianificazione, name="salva_pianificazione"),
    path("richieste/<int:pk>/budget/", views.decidi_budget, name="decidi_budget"),
    path("richieste/<int:pk>/sal/", views.aggiorna_sal, name="aggiorna_sal"),
    path("richieste/<int:pk>/rischio/analizza/", views.analizza_rischio, name="analizza_rischio"),
    path("richieste/<int:pk>/rischio/<str:tipo>/valida/", views.valida_rischio, name="valida_rischio"),
    path("richieste/<int:pk>/rischio/<str:tipo>/tratta/", views.tratta_rischio, name="tratta_rischio"),
]
