"""Esportazione in PowerPoint dei progetti filtrati.

Il mazzo nasce dal template aziendale (flusso/data/template_presentazione.pptx):
ne eredita copertina, retrocopertina, sfondo e font. Dentro ci mettiamo l'indice
delle aree, i numeri di sintesi e le schede a riquadri, con gli stessi valori che
si vedono a video. La griglia delle schede si adatta a quante sono: una sola sta
al centro e grande, due affiancate, e così via fino a sei.
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

CARD_PER_SLIDE = 6  # al massimo sei schede per slide

# Disposizione delle schede: quante colonne e righe per n schede nella slide.
GRIGLIA = {1: (1, 1), 2: (2, 1), 3: (3, 1), 4: (2, 2), 5: (3, 2), 6: (3, 2)}

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


def _apri_mazzo():
    """Presentazione vuota 16:9: il mazzo lo disegniamo noi, senza template esterni."""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    return prs


def _slide_vuota(prs):
    """Nuova slide sul layout vuoto."""
    return prs.slides.add_slide(prs.slide_layouts[6])


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


def _riquadro(slide, x, y, w, h, etichetta, valore, nota="", verde=False, dim_valore=11):
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
    run2.font.size = Pt(dim_valore)
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


def titolo_mazzo(per_area) -> str:
    """«Progetti Infosec» se l'export è di una sola area, «Progetti IT» se le contiene tutte."""
    presenti = [etichetta for area, etichetta in AREE if per_area.get(area)]
    return f"Progetti {presenti[0]}" if len(presenti) == 1 else "Progetti IT"


def _copertina(prs, titolo, sottotitolo, dati):
    """Copertina: fascia rossa col titolo, filtro applicato, data e logo."""
    from pptx.util import Inches

    data = (f"{timezone.localtime().strftime('%d/%m/%Y')} · "
            f"{_plurale(dati['progetti'], 'progetto', 'progetti')}, "
            f"{_plurale(dati['attivita'], 'attività', 'attività')}")

    slide = _slide_vuota(prs)
    _rettangolo(slide, 0, 0, prs.slide_width, Inches(2.2), fondo=ROSSO)
    tit = slide.shapes.add_textbox(Inches(0.78), Inches(0.62), Inches(11.5), Inches(1.0))
    _testo(tit, titolo, dim=38, grassetto=True, colore=BIANCO)
    sotto = slide.shapes.add_textbox(Inches(0.78), Inches(1.5), Inches(11.5), Inches(0.4))
    _testo(sotto, "ISEO Group · Portafoglio iniziative", dim=14, colore=BIANCO)

    riga = slide.shapes.add_textbox(Inches(0.78), Inches(2.55), Inches(9.0), Inches(0.4))
    _testo(riga, sottotitolo, dim=11, colore=GRIGIO)
    piede = slide.shapes.add_textbox(Inches(0.78), Inches(6.55), Inches(9.0), Inches(0.4))
    _testo(piede, data, dim=11, colore=GRIGIO)
    alt_logo = Inches(1.0)
    _logo(slide, Inches(13.333) - Inches(0.78) - alt_logo * 1000 / 489, Inches(2.5), alt_logo)
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


def _slide_indice(prs, titolo, ancore, conteggi):
    """Indice delle aree esportate, ognuna cliccabile verso la sua sezione."""
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt

    slide = _slide_vuota(prs)
    _intestazione(slide, "Indice", titolo)

    presenti = [(area, etichetta) for area, etichetta in AREE if conteggi.get(area)]
    altezza, passo = Inches(0.78), Inches(0.95)
    # Il blocco delle voci sta al centro verticale dell'area utile.
    y = Inches(1.7) + (Inches(4.7) - passo * len(presenti)) / 2
    for area, etichetta in presenti:
        riquadro = _rettangolo(slide, Inches(3.4), y, Inches(6.5), altezza, fondo=FONDO)
        tf = riquadro.text_frame
        tf.margin_left = Pt(16)
        par = tf.paragraphs[0]
        par.alignment = PP_ALIGN.LEFT
        run = par.add_run()
        run.text = etichetta
        run.font.size = Pt(16)
        run.font.bold = True
        run.font.color.rgb = _colore(ROSSO)
        coda = par.add_run()
        coda.text = "   " + _plurale(conteggi.get(area, 0), "scheda", "schede")
        coda.font.size = Pt(12)
        coda.font.color.rgb = _colore(GRIGIO)
        if area in ancore:
            _collega_a_slide(run, slide, ancore[area])
        y += passo
    return slide


def _intestazione(slide, titolo, occhiello=""):
    """Testata uguale su ogni slide: occhiello piccolo, titolo con filetto rosso, logo.

    Il titolo grande è quello della slide (l'indice, i numeri, la sezione di un'area);
    l'occhiello sopra ricorda di quale mazzo si tratta.
    """
    from pptx.util import Inches

    if occhiello:
        sopra = slide.shapes.add_textbox(Inches(0.78), Inches(0.42), Inches(9.5), Inches(0.28))
        _testo(sopra, occhiello, dim=10, grassetto=True, colore=GRIGIO, maiuscolo=True)
    _rettangolo(slide, Inches(0.6), Inches(0.76), Inches(0.09), Inches(0.44), fondo=ROSSO)
    box = slide.shapes.add_textbox(Inches(0.78), Inches(0.68), Inches(9.5), Inches(0.6))
    _testo(box, titolo, dim=22, grassetto=True)
    _logo(slide, Inches(11.95), Inches(0.55), Inches(0.42))


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


def _slide_aree(prs, per_area, dati, titolo):
    """Progetti e attività divisi per area, con i totali sotto."""
    from pptx.util import Inches

    presenti = [(area, etichetta) for area, etichetta in AREE if per_area.get(area)]
    slide = _slide_vuota(prs)
    _intestazione(slide, "Progetti e attività per area" if len(presenti) > 1
                  else "I numeri del portafoglio", titolo)

    n = max(len(presenti), 1)
    riga_aree = len(presenti) > 1  # con una sola area basterebbero i totali
    passo = Inches(0.25)
    larghezza = min(Inches(3.9), (Inches(12.13) - passo * (n - 1)) / n)
    partenza = (Inches(13.333) - (larghezza * n + passo * (n - 1))) / 2  # blocco centrato
    for i, (area, etichetta) in enumerate(presenti if riga_aree else []):
        numeri = riepilogo(per_area.get(area, []))
        forma = _tessera(slide, partenza + (larghezza + passo) * i, Inches(1.85),
                         larghezza, Inches(1.95), etichetta, str(numeri["n"]),
                         evidenzia=(area == "INFOSEC"))
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
    larghezza_t = Inches(2.95)
    partenza_t = (Inches(13.333) - (larghezza_t * 4 + passo * 3)) / 2
    y_totali = Inches(4.25) if riga_aree else Inches(3.45)
    for i, (etichetta, valore, nota) in enumerate(totali):
        _tessera(slide, partenza_t + (larghezza_t + passo) * i, y_totali,
                 larghezza_t, Inches(1.6), etichetta, valore, nota,
                 evidenzia=(i in (1, 2)))
    return slide


def _card(slide, richiesta, x, y, w, h):
    """Un quadrato progetto: testata, titolo, descrizione, effort e quattro riquadri.

    Le misure interne si ricavano da w e h, così la scheda regge sia grande al
    centro della slide (un progetto solo) sia piccola in una griglia da sei: i
    riquadri restano ancorati in basso e la descrizione prende lo spazio che resta.
    """
    from pptx.util import Inches

    _rettangolo(slide, x, y, w, h, fondo=BIANCO, bordo=(0xE5, 0xE7, 0xEB))
    grande = w >= Inches(5)
    bordo = Inches(0.18) if grande else Inches(0.13)
    largo = w - bordo * 2

    alt_testata = Inches(0.26) if grande else Inches(0.2)
    alt_titolo = Inches(0.62) if grande else Inches(0.4)
    quadr_h = Inches(0.72) if grande else Inches(0.5)
    alt_effort = Inches(0.28) if grande else Inches(0.22)

    testata = slide.shapes.add_textbox(x + bordo, y + bordo * 0.7, largo, alt_testata)
    _testo(testata,
           f"{richiesta.codice} · {richiesta.tipo_breve} · {richiesta.get_funzione_display()}"
           f"{' · Attività' if richiesta.is_attivita else ''} · {richiesta.stato_label}",
           dim=9 if grande else 7.5, grassetto=True, colore=GRIGIO)

    y_titolo = y + bordo * 0.7 + alt_testata
    titolo = slide.shapes.add_textbox(x + bordo, y_titolo, largo, alt_titolo)
    _testo(titolo, richiesta.titolo[:80], dim=17 if grande else 11.5, grassetto=True)

    quadr_w = (largo - Inches(0.12)) / 2
    base_y = y + h - bordo - quadr_h * 2 - Inches(0.07)
    effort_y = base_y - alt_effort - Inches(0.03)

    desc_y = y_titolo + alt_titolo + Inches(0.02)
    desc_h = effort_y - desc_y - Inches(0.03)
    if desc_h >= Inches(0.28):
        dim_desc = 10 if grande else 7.5
        righe = max(int(desc_h / (Inches(0.16) if grande else Inches(0.125))), 1)
        per_riga = int(largo / (Inches(0.068) if grande else Inches(0.05)))
        descrizione = " ".join((richiesta.descrizione or "").split())
        limite = max(righe * per_riga - 3, 20)
        if len(descrizione) > limite:
            descrizione = descrizione[:limite].rsplit(" ", 1)[0] + "…"
        box = slide.shapes.add_textbox(x + bordo, desc_y, largo, desc_h)
        _testo(box, descrizione, dim=dim_desc, colore=GRIGIO)

    effort = slide.shapes.add_textbox(x + bordo, effort_y, largo, alt_effort)
    _testo(effort, "Effort " + (richiesta.effort_fmt or "non ancora stimato"),
           dim=10 if grande else 8)

    riquadri = (
        ("Beneficio atteso", _euro(richiesta.saving_economico), False),
        ("Costo dell'attività" if richiesta.is_attivita else "Costo del progetto",
         _euro(richiesta.costo_iniziativa), False),
        ("Incremento qualitativo", richiesta.incremento_qualitativo_fmt or "—", True),
        ("Incremento efficienza", richiesta.incremento_efficienza_fmt or "—", True),
    )
    for i, (etichetta, valore, verde) in enumerate(riquadri):
        rx = x + bordo + (quadr_w + Inches(0.12)) * (i % 2)
        ry = base_y + (quadr_h + Inches(0.07)) * (i // 2)
        _riquadro(slide, rx, ry, quadr_w, quadr_h, etichetta, valore, verde=verde,
                  dim_valore=13 if grande else 10)


def _slide_schede(prs, titolo, occhiello, blocco, pagina, pagine):
    """Le schede di un blocco, in una griglia centrata che si adatta a quante sono."""
    from pptx.util import Inches

    slide = _slide_vuota(prs)
    _intestazione(slide, occhiello, titolo)
    if pagine > 1:
        num = slide.shapes.add_textbox(Inches(11.0), Inches(0.75), Inches(0.9), Inches(0.35))
        _testo(num, f"{pagina} / {pagine}", dim=10, colore=GRIGIO)

    colonne, righe = GRIGLIA.get(len(blocco), (3, 2))
    passo = Inches(0.22)
    area_w, area_h = Inches(12.2), Inches(5.45)
    w = min((area_w - passo * (colonne - 1)) / colonne, Inches(7.8))
    h = min((area_h - passo * (righe - 1)) / righe, Inches(4.6))
    # Blocco centrato in orizzontale e in verticale nell'area utile.
    x0 = (Inches(13.333) - (w * colonne + passo * (colonne - 1))) / 2
    y0 = Inches(1.62) + (area_h - (h * righe + passo * (righe - 1))) / 2
    for i, richiesta in enumerate(blocco):
        riga, colonna = divmod(i, colonne)
        _card(slide, richiesta, x0 + (w + passo) * colonna, y0 + (h + passo) * riga, w, h)
    return slide


def nome_file(filtri) -> str:
    parti = ["progetti", slugify(filtri.get("tipo_label") or "tutti")]
    if filtri.get("stato_label"):
        parti.append(slugify(filtri["stato_label"]))
    parti.append(timezone.localdate().strftime("%Y%m%d"))
    return "-".join(p for p in parti if p) + ".pptx"


def risposta_pptx(richieste, filtri, descrizione) -> HttpResponse:
    """Costruisce il mazzo e lo restituisce come allegato .pptx."""
    prs = _apri_mazzo()

    dati = riepilogo(richieste)
    per_area = {area: [r for r in richieste if r.tipo == area] for area, _ in AREE}
    titolo = titolo_mazzo(per_area)
    conteggi = {area: len(per_area.get(area) or []) for area, _ in AREE}

    _copertina(prs, titolo, descrizione, dati)
    # L'indice serve solo se ci sono più aree da raggiungere: con una sola area
    # il mazzo è già tutto lì e una pagina di rimandi sarebbe rumore.
    aree_presenti = sum(1 for n in conteggi.values() if n)
    indice = _slide_indice(prs, titolo, {}, conteggi) if aree_presenti > 1 else None
    _slide_aree(prs, per_area, dati, titolo)

    ancore = {}
    for area, etichetta in AREE:
        voci = per_area.get(area) or []
        if not voci:
            continue
        blocchi = [voci[i:i + CARD_PER_SLIDE] for i in range(0, len(voci), CARD_PER_SLIDE)]
        for n, blocco in enumerate(blocchi, start=1):
            slide = _slide_schede(prs, titolo, f"{etichetta} · schede progetto",
                                  blocco, n, len(blocchi))  # occhiello = titolo del mazzo
            ancore.setdefault(area, slide)
    if indice is not None:
        _aggancia_indice(indice, ancore)

    risposta = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
    risposta["Content-Disposition"] = f'attachment; filename="{nome_file(filtri)}"'
    prs.save(risposta)
    return risposta


def _aggancia_indice(slide, ancore):
    """Trasforma le voci dell'indice in link, ora che le sezioni esistono."""
    etichette = {etichetta: area for area, etichetta in AREE}
    for forma in slide.shapes:
        if not forma.has_text_frame:
            continue
        for paragrafo in forma.text_frame.paragraphs:
            for run in paragrafo.runs:
                area = etichette.get(run.text.strip())
                if area and area in ancore:
                    _collega_a_slide(run, slide, ancore[area])
