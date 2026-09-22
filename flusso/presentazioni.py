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

# Ordine delle sezioni nel mazzo: Infosec apre, poi le altre.
AREE = (("INFOSEC", "Infosec"), ("AI", "AI"), ("APPLICATION", "Application"),
        ("IT_OPERATION", "IT Operation"))


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


def _logo(slide, x, y, altezza):
    """Mette il logo ISEO, se il file statico c'è (rapporto 1000x489)."""
    from pathlib import Path

    from django.conf import settings
    from pptx.util import Emu

    percorso = Path(settings.BASE_DIR) / "static" / "img" / "iseo-logo.png"
    if not percorso.exists():
        return None
    return slide.shapes.add_picture(str(percorso), x, y, height=altezza,
                                    width=Emu(int(altezza * 1000 / 489)))


def _plurale(n, singolare, plurale) -> str:
    return f"{n} {singolare if n == 1 else plurale}"


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
    """Prima slide: titolo, filtro applicato e — riempito dopo — l'indice delle aree."""
    from pptx.util import Inches

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _rettangolo(slide, 0, 0, prs.slide_width, Inches(2.0), fondo=ROSSO)
    titolo = slide.shapes.add_textbox(Inches(0.7), Inches(0.5), Inches(11.9), Inches(1.0))
    _testo(titolo, "Progetti IT", dim=38, grassetto=True, colore=BIANCO)
    sotto = slide.shapes.add_textbox(Inches(0.7), Inches(1.35), Inches(11.9), Inches(0.5))
    _testo(sotto, "ISEO Group · Portafoglio iniziative", dim=14, colore=BIANCO)

    _logo(slide, Inches(9.6), Inches(2.45), Inches(1.15))
    riga = slide.shapes.add_textbox(Inches(0.7), Inches(2.25), Inches(8.5), Inches(0.4))
    _testo(riga, filtri, dim=11, colore=GRIGIO)
    data = slide.shapes.add_textbox(Inches(0.7), Inches(6.7), Inches(11.9), Inches(0.4))
    _testo(data, f"Esportato il {timezone.localtime().strftime('%d/%m/%Y')} · "
                 f"{_plurale(dati['progetti'], 'progetto', 'progetti')}, "
                 f"{_plurale(dati['attivita'], 'attività', 'attività')}",
           dim=11, colore=GRIGIO)
    return slide


def _collega_a_slide(run, slide_origine, slide_destinazione):
    """Rende il testo un link interno alla slide indicata (indice cliccabile)."""
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    from pptx.oxml.ns import qn

    rId = slide_origine.part.relate_to(slide_destinazione.part, RT.SLIDE)
    rPr = run._r.get_or_add_rPr()
    link = rPr.makeelement(qn("a:hlinkClick"),
                           {qn("r:id"): rId, "action": "ppaction://hlinksldjump"})
    rPr.append(link)


def _indice(slide, ancore, conteggi):
    """Indice sulla copertina: una riga per area, cliccabile se l'area ha schede."""
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt

    y = Inches(2.9)
    for area, etichetta in AREE:
        n = conteggi.get(area, 0)
        riquadro = _rettangolo(slide, Inches(0.7), y, Inches(5.4), Inches(0.62),
                               fondo=FONDO if n else BIANCO,
                               bordo=None if n else (0xE5, 0xE7, 0xEB))
        tf = riquadro.text_frame
        tf.margin_left = Pt(12)
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT
        run = p.add_run()
        run.text = f"{etichetta}"
        run.font.size = Pt(14)
        run.font.bold = True
        run.font.color.rgb = _colore(ROSSO if n else GRIGIO)
        coda = p.add_run()
        coda.text = "   " + _plurale(n, "scheda", "schede")
        coda.font.size = Pt(11)
        coda.font.color.rgb = _colore(GRIGIO)
        if area in ancore:
            _collega_a_slide(run, slide, ancore[area])
        y += Inches(0.78)


def _tessera(slide, x, y, w, h, etichetta, valore, nota="", evidenzia=False):
    """Tessera dei numeri: etichetta piccola in alto, valore grande, nota sotto."""
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Pt

    forma = _rettangolo(slide, x, y, w, h)
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
    r2.font.size = Pt(24)
    r2.font.bold = True
    r2.font.color.rgb = _colore(ROSSO if evidenzia else INCHIOSTRO)
    if nota:
        p3 = tf.add_paragraph()
        p3.alignment = PP_ALIGN.LEFT
        r3 = p3.add_run()
        r3.text = nota
        r3.font.size = Pt(8)
        r3.font.color.rgb = _colore(GRIGIO)
    return forma


def _slide_aree(prs, per_area, dati):
    """Seconda slide: progetti e attività divisi per area, più i totali."""
    from pptx.util import Inches

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _logo(slide, Inches(11.9), Inches(0.35), Inches(0.42))
    tit = slide.shapes.add_textbox(Inches(0.6), Inches(0.35), Inches(10), Inches(0.6))
    _testo(tit, "Progetti e attività per area", dim=24, grassetto=True)

    larghezza, altezza = Inches(2.95), Inches(1.95)
    for i, (area, etichetta) in enumerate(AREE):
        voci = per_area.get(area, [])
        numeri = riepilogo(voci)
        x = Inches(0.6) + (larghezza + Inches(0.25)) * i
        forma = _tessera(slide, x, Inches(1.3), larghezza, altezza, etichetta,
                         str(numeri["n"]), evidenzia=(area == "INFOSEC"))
        from pptx.enum.text import PP_ALIGN
        from pptx.util import Pt
        tf = forma.text_frame
        for testo in (f"{_plurale(numeri['progetti'], 'progetto', 'progetti')} · "
                      f"{_plurale(numeri['attivita'], 'attività', 'attività')}",
                      f"Costo {_euro(numeri['costo'])}",
                      f"Effort {numeri['effort_gg']:g} gg".replace(".", ","),
                      f"Beneficio atteso {_euro(numeri['beneficio'])}"):
            par = tf.add_paragraph()
            par.alignment = PP_ALIGN.LEFT
            run = par.add_run()
            run.text = testo
            run.font.size = Pt(9.5)
            run.font.color.rgb = _colore(GRIGIO)

    totali = [
        ("Totale schede", str(dati["n"]),
         f"{_plurale(dati['progetti'], 'progetto', 'progetti')} · "
         f"{_plurale(dati['attivita'], 'attività', 'attività')}"),
        ("Costo totale", _euro(dati["costo"]),
         f"{dati['senza_costo']} senza costo definito" if dati["senza_costo"] else ""),
        ("Beneficio atteso", _euro(dati["beneficio"]), ""),
        ("Effort", f"{dati['effort_gg']:g} gg".replace(".", ","), "a 8 ore/giorno"),
    ]
    for i, (etichetta, valore, nota) in enumerate(totali):
        x = Inches(0.6) + (larghezza + Inches(0.25)) * i
        _tessera(slide, x, Inches(3.7), larghezza, Inches(1.4), etichetta, valore, nota,
                 evidenzia=(i in (1, 2)))


def _card(slide, richiesta, x, y, w, h):
    from pptx.util import Inches, Pt

    _rettangolo(slide, x, y, w, h, fondo=BIANCO, bordo=(0xE5, 0xE7, 0xEB))
    testata = slide.shapes.add_textbox(x + Inches(0.15), y + Inches(0.1), w - Inches(0.3), Inches(0.3))
    _testo(testata,
           f"{richiesta.codice} · {richiesta.tipo_breve} · {richiesta.get_funzione_display()}"
           f"{' · Attività' if richiesta.is_attivita else ''} · {richiesta.stato_label}",
           dim=8, grassetto=True, colore=GRIGIO)

    titolo = slide.shapes.add_textbox(x + Inches(0.15), y + Inches(0.36), w - Inches(0.3), Inches(0.5))
    _testo(titolo, richiesta.titolo[:70], dim=12.5, grassetto=True)

    effort = slide.shapes.add_textbox(x + Inches(0.15), y + Inches(0.78), w - Inches(0.3), Inches(0.22))
    _testo(effort, "Effort " + (richiesta.effort_fmt or "non ancora stimato"),
           dim=8.5, colore=GRIGIO)

    quadr_w = (w - Inches(0.45)) / 2
    quadr_h = Inches(0.62)
    base_y = y + Inches(1.05)
    _riquadro(slide, x + Inches(0.15), base_y, quadr_w, quadr_h,
              "Beneficio atteso", _euro(richiesta.saving_economico))
    _riquadro(slide, x + Inches(0.3) + quadr_w, base_y, quadr_w, quadr_h,
              "Costo dell'attività" if richiesta.is_attivita else "Costo del progetto",
              _euro(richiesta.costo_iniziativa))
    _riquadro(slide, x + Inches(0.15), base_y + quadr_h + Inches(0.1), quadr_w, quadr_h,
              "Incremento qualitativo", richiesta.incremento_qualitativo_fmt or "—", verde=True)
    _riquadro(slide, x + Inches(0.3) + quadr_w, base_y + quadr_h + Inches(0.1), quadr_w, quadr_h,
              "Incremento efficienza", richiesta.incremento_efficienza_fmt or "—", verde=True)


def _slide_schede(prs, area_label, blocco, pagina, pagine):
    from pptx.util import Inches

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _rettangolo(slide, Inches(0.55), Inches(0.3), Inches(0.09), Inches(0.42), fondo=ROSSO)
    tit = slide.shapes.add_textbox(Inches(0.75), Inches(0.25), Inches(10), Inches(0.5))
    _testo(tit, area_label, dim=20, grassetto=True)
    _logo(slide, Inches(11.9), Inches(0.28), Inches(0.42))
    num = slide.shapes.add_textbox(Inches(10.5), Inches(0.32), Inches(1.2), Inches(0.4))
    _testo(num, f"{pagina} / {pagine}", dim=10, colore=GRIGIO)

    w, h = Inches(4.0), Inches(2.5)
    for i, richiesta in enumerate(blocco):
        x = Inches(0.55) + (w + Inches(0.15)) * (i % 3)
        y = Inches(1.05) + (h + Inches(0.45)) * (i // 3)
        _card(slide, richiesta, x, y, w, h)
    return slide


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
    per_area = {area: [r for r in richieste if r.tipo == area] for area, _ in AREE}
    copertina = _copertina(prs, descrizione, dati)
    _slide_aree(prs, per_area, dati)

    # Una sezione per area, nell'ordine deciso: Infosec apre il mazzo.
    ancore = {}
    for area, etichetta in AREE:
        voci = per_area.get(area) or []
        if not voci:
            continue
        blocchi = [voci[i:i + CARD_PER_SLIDE] for i in range(0, len(voci), CARD_PER_SLIDE)]
        for n, blocco in enumerate(blocchi, start=1):
            slide = _slide_schede(prs, f"{etichetta} — schede progetto", blocco, n, len(blocchi))
            ancore.setdefault(area, slide)
    _indice(copertina, ancore, {area: len(per_area.get(area) or []) for area, _ in AREE})

    risposta = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
    risposta["Content-Disposition"] = f'attachment; filename="{nome_file(filtri)}"'
    prs.save(risposta)
    return risposta
