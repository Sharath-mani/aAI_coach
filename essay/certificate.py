import os
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch


def generate_pass_certificate(candidate_name: str,
                              scores_percent: dict,
                              essay_topic: str | None = None,
                              certificate_number: str = "CERT-0001",
                              filename: str = "certificate.pdf",
                              logo_path: str = "logo.png"):  # pragma: no cover
    """Generate a PDF certificate for the user."""
    doc = SimpleDocTemplate(filename, pagesize=A4, topMargin=0.5 * inch, bottomMargin=0.5 * inch,
                            leftMargin=0.5 * inch, rightMargin=0.5 * inch)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle('TitleStyle', parent=styles['Title'], fontSize=26,
                                 alignment=1, textColor=colors.HexColor('#0A4FA3'), spaceAfter=8)
    subtitle_style = ParagraphStyle('SubtitleStyle', parent=styles['Heading2'], alignment=1,
                                    textColor=colors.HexColor('#1F77D0'), fontSize=14, spaceAfter=4)
    name_style = ParagraphStyle('NameStyle', alignment=1, fontSize=22,
                                textColor=colors.HexColor('#0A4FA3'), spaceAfter=12)
    body_style = ParagraphStyle('BodyStyle', parent=styles['BodyText'], alignment=1,
                                fontSize=10.5, textColor=colors.HexColor('#2E3440'), spaceAfter=8, leading=13)

    def add_watermark(canvas_obj, doc_obj):
        try:
            canvas_obj.saveState()
            canvas_obj.setFillAlpha(0.06)
            canvas_obj.drawImage(logo_path, x=170, y=250, width=250, height=250,
                                 preserveAspectRatio=True, mask='auto')
            canvas_obj.restoreState()
        except Exception:
            pass

    top_bar = Table([['']], colWidths=[7.5 * inch], rowHeights=[3])
    top_bar.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#0A4FA3')),
                                 ('TOPPADDING', (0, 0), (-1, -1), 0),
                                 ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))
    story.append(top_bar)
    story.append(Spacer(1, 0.2 * inch))

    try:
        logo = Image(logo_path, width=1.0 * inch, height=1.0 * inch)
        logo.hAlign = 'CENTER'
        story.append(logo)
    except Exception:
        story.append(Paragraph('<i>AgenixAi</i>', body_style))

    story.append(Spacer(1, 0.12 * inch))
    story.append(Paragraph('<b>AgenixAi</b>', subtitle_style))
    story.append(Paragraph('Certificate of Achievement', title_style))
    story.append(Paragraph('Awarded To', subtitle_style))
    story.append(Paragraph(f'<b>{candidate_name}</b>', name_style))

    if essay_topic:
        award_text = (f'This certificate is proudly presented for achieving outstanding performance '
                      f'scoring <b>70% or above</b> in all evaluation categories for essay: <i>{essay_topic}</i>.')
    else:
        award_text = (f'This certificate is proudly presented for achieving outstanding performance '
                      f'scoring <b>70% or above</b> in all evaluation categories across the session.')

    story.append(Paragraph(award_text, body_style))
    story.append(Spacer(1, 0.12 * inch))

    details = ' | '.join(f'{k}: {v:.1f}%' for k, v in scores_percent.items()) if scores_percent else 'No scores available'
    story.append(Paragraph(f'<b>Scores:</b> {details}', body_style))
    story.append(Spacer(1, 0.12 * inch))

    story.append(Paragraph(f'<b>Certificate Number:</b> {certificate_number}', body_style))
    story.append(Spacer(1, 0.12 * inch))

    doc.build(story, onFirstPage=add_watermark, onLaterPages=add_watermark)

    return filename
