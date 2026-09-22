"""Esportazione in PowerPoint dei progetti filtrati.

Una slide di copertina, una di KPI e poi le schede a riquadri, sei per slide:
gli stessi numeri che si vedono a video (beneficio atteso, costo, incrementi),
cosi' il mazzo si porta in riunione senza rimettere insieme i dati a mano.
"""

from django.http import HttpResponse
from django.utils import timezone
from django.utils.text import slugify

# Palette ISEO, la stessa dell'interfaccia.
ROSSO = (0xC8, 0x10, 0x2E)
INCHIOSTRO = (0x1A, 0x1D, 0x21)
GRIGIO = (0x6B, 0x72, 0x80)
FONDO = (0xF7, 0xF8, 0xF9)
VERDE_BG = (0xEC, 0xF5, 0xEE)
VERDE_FG = (0x1E, 0x7A, 0x3C)
BIANCO = (0xFF, 0xFF, 0xFF)

CARD_PER_SLIDE = 6  # 3 colonne x 2 righe


def _colore(rgb):
    from pptx.dml.color import RGBColor

    return RGBColor(*rgb)


def _testo(contenitore, testo, *, dim=12, grassetto=False, colore=INCHIOSTRO,
           maiuscolo=False, spaziatura=0):
    from pptx.util import Pt

    tf = contenitore.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0] if not tf.paragraphs[0].runs and tf.paragraphs[0].text == "" \
        else tf.add_paragraph()
    run = p.add_run()
    run.text = (testo or "").upper() if maiuscolo else (testo or "")
    run.font.size = Pt(dim)
    run.font.bold = grassetto
    run.font.color.rgb = _colore(colore)
    p.space_after = Pt(spaziatura)
    return p


def _rettangolo(slide, x, y, w, h, fondo=FONDO, bordo=None):
    from pptx.enum.shapes import MSO_SHAPE

    forma = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    forma.fill.solid()
    forma.fill.fore_color.rgb = _colore(fondo)
    if bordo is None:
        forma.line.fill.background()
    else:
        forma.line.color.rgb = _colore(bordo)
    forma.shadow.inherit = False
    forma.adjustments[0] = 0.08
    forma.text_frame.word_wrap = True
    return forma


def _euro(valore) -> str:
    if valore is None:
        return "—"
    return "€ " + f"{float(valore):,.0f}".replace(",", ".")


def _riquadro(slide, x, y, w, h, etichetta, valore, nota="", verde=False):
    """Uno dei quadretti della scheda: etichetta piccola, valore in evidenza."""
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Pt

    forma = _rettangolo(slide, x, y, w, h, fondo=VERDE_BG if verde else FONDO)
    tf = forma.text_frame
    tf.margin_left = tf.margin_right = Pt(6)
    tf.margin_top = tf.margin_bottom = Pt(4)
    tf.vertical_anchor = MSO_ANCHOR.TOP
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    run = p.add_run()
    run.text = etichetta.upper()
    run.font.size = Pt(7)
    run.font.bold = True
    run.font.color.rgb = _colore(GRIGIO)
    p2 = tf.add_paragraph()
    p2.alignment = PP_ALIGN.LEFT
    run2 = p2.add_run()
    run2.text = valore
    run2.font.size = Pt(11)
    run2.font.bold = True
    run2.font.color.rgb = _colore(VERDE_FG if verde else INCHIOSTRO)
    if nota:
        p3 = tf.add_paragraph()
        p3.alignment = PP_ALIGN.LEFT
        run3 = p3.add_run()
        run3.text = nota[:60]
        run3.font.size = Pt(6.5)
        run3.font.color.rgb = _colore(GRIGIO)
    return forma


def riepilogo(richieste) -> dict:
    """Numeri di sintesi del sottoinsieme esportato (non i KPI globali)."""
    from .models import NaturaVoce

    costi = [r.costo_iniziativa for r in richieste if r.costo_iniziativa is not None]
    benefici = [r.saving_economico for r in richieste if r.saving_economico is not None]
    a_budget = sum(1 for r in richieste if r.esito_budget == "A_BUDGET")
    extra = sum(1 for r in richieste if r.esito_budget == "EXTRA_BUDGET")
    attivita = sum(1 for r in richieste if r.natura == NaturaVoce.ATTIVITA)
    ore = sum(r.effort_ore or 0 for r in richieste)
    return {
        "n": len(richieste),
        "attivita": attivita,
        "progetti": len(richieste) - attivita,
        "costo": sum(costi) if costi else None,
        "beneficio": sum(benefici) if benefici else None,
        "effort_gg": round(ore / 8, 1) if ore else 0,
        "a_budget": a_budget,
        "extra": extra,
        "senza_costo": len(richieste) - len(costi),
    }


def _copertina(prs, filtri, dati):
    from pptx.util import Inches, Pt

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _rettangolo(slide, 0, 0, prs.slide_width, Inches(2.2), fondo=ROSSO)
    titolo = slide.shapes.add_textbox(Inches(0.7), Inches(0.6), Inches(11.9), Inches(1.0))
    _testo(titolo, "Progetti Digital Transformation", dim=34, grassetto=True, colore=BIANCO)
    sotto = slide.shapes.add_textbox(Inches(0.7), Inches(1.5), Inches(11.9), Inches(0.5))
    _testo(sotto, "ISEO Group · Portafoglio iniziative", dim=14, colore=BIANCO)

    riga = slide.shapes.add_textbox(Inches(0.7), Inches(2.6), Inches(11.9), Inches(0.6))
    _testo(riga, filtri, dim=12, colore=GRIGIO)
    data = slide.shapes.add_textbox(Inches(0.7), Inches(6.6), Inches(11.9), Inches(0.4))
    _testo(data, f"Esportato il {timezone.localtime().strftime('%d/%m/%Y')} · "
                 f"{dati['progetti']} progetti, {dati['attivita']} attività",
           dim=11, colore=GRIGIO)


def _slide_kpi(prs, dati):
    from pptx.util import Inches

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    tit = slide.shapes.add_textbox(Inches(0.6), Inches(0.4), Inches(12), Inches(0.6))
    _testo(tit, "I numeri del portafoglio", dim=24, grassetto=True)

    tiles = [
        ("Progetti", str(dati["progetti"]), ""),
        ("Attività", str(dati["attivita"]), "fuori da KPI e costi"),
        ("Costo totale", _euro(dati["costo"]),
         f"{dati['senza_costo']} senza costo definito" if dati["senza_costo"] else ""),
        ("Beneficio atteso", _euro(dati["beneficio"]), ""),
        ("Effort", f"{dati['effort_gg']:g} gg".replace(".", ","), "a 8 ore/giorno"),
        ("A budget / extra", f"{dati['a_budget']} / {dati['extra']}", "copertura decisa"),
    ]
    larghezza, altezza = Inches(3.9), Inches(1.25)
    for i, (etichetta, valore, nota) in enumerate(tiles):
        x = Inches(0.6) + (larghezza + Inches(0.3)) * (i % 3)
        y = Inches(1.45) + (altezza + Inches(0.45)) * (i // 3)
        forma = _rettangolo(slide, x, y, larghezza, altezza)
        from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
        from pptx.util import Pt
        tf = forma.text_frame
        tf.margin_left = tf.margin_top = Pt(12)
        tf.vertical_anchor = MSO_ANCHOR.TOP
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT
        r = p.add_run()
        r.text = etichetta.upper()
        r.font.size = Pt(9)
        r.font.bold = True
        r.font.color.rgb = _colore(GRIGIO)
        p2 = tf.add_paragraph()
        p2.alignment = PP_ALIGN.LEFT
        r2 = p2.add_run()
        r2.text = valore
        r2.font.size = Pt(26)
        r2.font.bold = True
        r2.font.color.rgb = _colore(ROSSO if i in (2, 3) else INCHIOSTRO)
        if nota:
            p3 = tf.add_paragraph()
            p3.alignment = PP_ALIGN.LEFT
            r3 = p3.add_run()
            r3.text = nota
            r3.font.size = Pt(8)
            r3.font.color.rgb = _colore(GRIGIO)


def _card(slide, richiesta, x, y, w, h):
    from pptx.util import Inches, Pt

    _rettangolo(slide, x, y, w, h, fondo=BIANCO, bordo=(0xE5, 0xE7, 0xEB))
    testata = slide.shapes.add_textbox(x + Inches(0.15), y + Inches(0.1), w - Inches(0.3), Inches(0.3))
    _testo(testata,
           f"{richiesta.codice} · {richiesta.tipo_breve} · {richiesta.get_funzione_display()}"
           f"{' · Attività' if richiesta.is_attivita else ''} · {richiesta.stato_label}",
           dim=8, grassetto=True, colore=GRIGIO)

    titolo = slide.shapes.add_textbox(x + Inches(0.15), y + Inches(0.38), w - Inches(0.3), Inches(0.55))
    _testo(titolo, richiesta.titolo[:70], dim=12.5, grassetto=True)

    quadr_w = (w - Inches(0.45)) / 2
    quadr_h = Inches(0.62)
    base_y = y + Inches(1.0)
    _riquadro(slide, x + Inches(0.15), base_y, quadr_w, quadr_h,
              "Beneficio atteso", _euro(richiesta.saving_economico))
    _riquadro(slide, x + Inches(0.3) + quadr_w, base_y, quadr_w, quadr_h,
              "Costo dell'attività" if richiesta.is_attivita else "Costo del progetto",
              _euro(richiesta.costo_iniziativa))
    _riquadro(slide, x + Inches(0.15), base_y + quadr_h + Inches(0.1), quadr_w, quadr_h,
              "Incremento qualitativo", richiesta.incremento_qualitativo_fmt or "—", verde=True)
    _riquadro(slide, x + Inches(0.3) + quadr_w, base_y + quadr_h + Inches(0.1), quadr_w, quadr_h,
              "Incremento efficienza", richiesta.incremento_efficienza_fmt or "—", verde=True)


def _slide_schede(prs, blocco, pagina, pagine):
    from pptx.util import Inches

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    tit = slide.shapes.add_textbox(Inches(0.6), Inches(0.25), Inches(10), Inches(0.5))
    _testo(tit, "Le schede progetto", dim=20, grassetto=True)
    num = slide.shapes.add_textbox(Inches(11.3), Inches(0.3), Inches(1.6), Inches(0.4))
    _testo(num, f"{pagina} / {pagine}", dim=10, colore=GRIGIO)

    w, h = Inches(4.0), Inches(2.5)
    for i, richiesta in enumerate(blocco):
        x = Inches(0.55) + (w + Inches(0.15)) * (i % 3)
        y = Inches(1.05) + (h + Inches(0.45)) * (i // 3)
        _card(slide, richiesta, x, y, w, h)


def nome_file(filtri) -> str:
    parti = ["progetti", slugify(filtri.get("tipo_label") or "tutti")]
    if filtri.get("stato_label"):
        parti.append(slugify(filtri["stato_label"]))
    parti.append(timezone.localdate().strftime("%Y%m%d"))
    return "-".join(p for p in parti if p) + ".pptx"


def risposta_pptx(richieste, filtri, descrizione) -> HttpResponse:
    """Costruisce il mazzo e lo restituisce come allegato .pptx."""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)  # 16:9

    dati = riepilogo(richieste)
    _copertina(prs, descrizione, dati)
    _slide_kpi(prs, dati)

    blocchi = [richieste[i:i + CARD_PER_SLIDE]
               for i in range(0, len(richieste), CARD_PER_SLIDE)]
    for n, blocco in enumerate(blocchi, start=1):
        _slide_schede(prs, blocco, n, len(blocchi))

    risposta = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
    risposta["Content-Disposition"] = f'attachment; filename="{nome_file(filtri)}"'
    prs.save(risposta)
    return risposta
