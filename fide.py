"""FIDE initial rating calculation and PDF certificate generation."""
from io import BytesIO
from datetime import datetime


# ---------------------------------------------------------------------------
# dp table (FIDE B.02 2024)
# ---------------------------------------------------------------------------

_DP = {
    1.00:800,0.99:677,0.98:589,0.97:538,0.96:501,0.95:470,0.94:444,0.93:422,0.92:401,0.91:383,
    0.90:366,0.89:351,0.88:336,0.87:322,0.86:309,0.85:296,0.84:284,0.83:273,0.82:262,0.81:251,
    0.80:240,0.79:230,0.78:220,0.77:211,0.76:202,0.75:193,0.74:184,0.73:175,0.72:166,0.71:158,
    0.70:149,0.69:141,0.68:133,0.67:125,0.66:117,0.65:110,0.64:102,0.63:95,0.62:87,0.61:80,
    0.60:72,0.59:65,0.58:57,0.57:50,0.56:43,0.55:36,0.54:29,0.53:21,0.52:14,0.51:7,0.50:0,
    0.49:-7,0.48:-14,0.47:-21,0.46:-29,0.45:-36,0.44:-43,0.43:-50,0.42:-57,0.41:-65,0.40:-72,
    0.39:-80,0.38:-87,0.37:-95,0.36:-102,0.35:-110,0.34:-117,0.33:-125,0.32:-133,0.31:-141,
    0.30:-149,0.29:-158,0.28:-166,0.27:-175,0.26:-184,0.25:-193,0.24:-202,0.23:-211,0.22:-220,
    0.21:-230,0.20:-240,0.19:-251,0.18:-262,0.17:-273,0.16:-284,0.15:-296,0.14:-309,0.13:-322,
    0.12:-336,0.11:-351,0.10:-366,0.09:-383,0.08:-401,0.07:-422,0.06:-444,0.05:-470,0.04:-501,
    0.03:-538,0.02:-589,0.01:-677,0.00:-800,
}


def _get_dp(p: float) -> int:
    p_r = round(p, 2)
    for threshold in sorted(_DP.keys(), reverse=True):
        if p_r >= threshold:
            return _DP[threshold]
    return -800


def calculate_rating(opponents: list, results: list) -> dict | None:
    """
    Calculate FIDE initial rating from real games only.
    The two fictitious 1800-draw games are added mathematically (+2 games,
    +3600 to rating sum, +1 to score) per FIDE B.02.2024 §7.1.4.
    Returns None if fewer than 5 valid games are provided.
    """
    valid_opp, valid_res = [], []

    for opp, res in zip(opponents, results):
        try:
            opp_val = int(float(opp))
            if not (800 <= opp_val <= 3000):
                continue
        except (ValueError, TypeError):
            continue

        try:
            res_f = float(res)
            score = 1.0 if res_f >= 1 else (0.5 if res_f == 0.5 else 0.0)
        except (ValueError, TypeError):
            s = str(res).strip().lower()
            score = 1.0 if s in ("1", "win") else (0.5 if s in ("0.5", "draw", "½", "=") else 0.0)

        valid_opp.append(opp_val)
        valid_res.append(score)

    if len(valid_opp) < 5:
        return None

    n     = len(valid_opp) + 2          # +2 fictitious games
    avg   = (sum(valid_opp) + 3600) // n
    score = sum(valid_res) + 1.0        # +1 for two 0.5 draws
    perc  = score / n
    dp    = _get_dp(perc)
    Rp    = avg + dp
    rating = max(Rp, 1000)

    if avg >= 2000 and perc >= 0.50:
        rating = max(rating, 1600)
    if Rp >= 2250:
        rating = max(rating, 1800)
    if Rp >= 2400:
        rating = max(rating, 2000)

    return {
        "rating":     round(rating),
        "Rp":         round(Rp),
        "avg":        avg,
        "score":      round(score, 1),
        "games":      n,
        "real_games": len(valid_opp),
        "perc":       round(perc * 100, 1),
        "dp":         dp,
    }


def generate_pdf(data: dict, name: str) -> BytesIO:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=1.5 * inch)
    styles = getSampleStyleSheet()

    def ps(name_, **kw):
        return ParagraphStyle(name_, parent=styles["Normal"], **kw)

    story = [
        Paragraph("FIDE Initial Rating Certificate",
                  ps("H1", fontSize=28, alignment=1, spaceAfter=30, textColor=colors.HexColor("#003087"))),
        Paragraph(name or "Chess Player",
                  ps("Name", fontSize=22, alignment=1, spaceAfter=20)),
        Paragraph(f"<b>{data['rating']}</b>",
                  ps("Big", fontSize=72, alignment=1, textColor=colors.HexColor("#003087"))),
        Paragraph("First Official FIDE Rating",
                  ps("Sub", fontSize=16, alignment=1, spaceAfter=40)),
    ]

    table_data = [
        ["Games Played",        str(data["games"])],
        ["Average Opponent",    str(data["avg"])],
        ["Score",               f"{data['score']}/{data['games']} ({data['perc']}%)"],
        ["Performance Rating",  str(data["Rp"])],
        ["Date",                datetime.now().strftime("%B %d, %Y")],
    ]
    t = Table(table_data, colWidths=[3.5 * inch, 2.8 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003087")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f0f8ff")),
        ("GRID",       (0, 0), (-1, -1), 1, colors.lightgrey),
        ("FONTSIZE",   (0, 0), (-1, -1), 13),
        ("LEFTPADDING",(0, 0), (-1, -1), 20),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(t)
    story.append(Spacer(1, 40))
    story.append(Paragraph(
        "Per FIDE Rating Regulations effective March 1, 2024",
        ps("Footer", alignment=1, fontSize=11, textColor=colors.grey),
    ))
    doc.build(story)
    buffer.seek(0)
    return buffer
