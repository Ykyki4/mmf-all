"""Генерация файлов отчётов: PDF (reportlab), Excel (openpyxl), CSV."""

import csv
import io
from functools import lru_cache
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..schemas.hr import ReportChart, ReportData

_FONT_DIRS = [Path("/usr/share/fonts/truetype/dejavu"), Path("C:/Windows/Fonts")]
_BRAND_COLOR = colors.HexColor("#1F4E79")
_HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
_MAX_CHART_LABELS = 12


# ── CSV ───────────────────────────────────────────────────────────────────────

def render_csv(rows: list[list[Any]]) -> bytes:
    """CSV с BOM и разделителем «;» — корректно открывается в русском Excel."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


# ── PDF ───────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _pdf_fonts() -> tuple[str, str]:
    """Регистрирует шрифт с кириллицей (DejaVu Sans), при отсутствии — встроенный Helvetica."""
    for font_dir in _FONT_DIRS:
        regular, bold = font_dir / "DejaVuSans.ttf", font_dir / "DejaVuSans-Bold.ttf"
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("DejaVuSans", str(regular)))
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", str(bold)))
            return "DejaVuSans", "DejaVuSans-Bold"
    return "Helvetica", "Helvetica-Bold"


def _pdf_styles() -> dict[str, ParagraphStyle]:
    regular, bold = _pdf_fonts()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontName=bold, fontSize=16, textColor=_BRAND_COLOR),
        "heading": ParagraphStyle("heading", parent=base["Heading2"], fontName=bold, fontSize=12,
                                  textColor=_BRAND_COLOR, spaceBefore=8),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontName=regular, fontSize=9, leading=12),
        "cell": ParagraphStyle("cell", parent=base["BodyText"], fontName=regular, fontSize=8, leading=10),
        "cell_bold": ParagraphStyle("cell_bold", parent=base["BodyText"], fontName=bold, fontSize=8,
                                    leading=10, textColor=colors.white),
    }


def _escape(value: Any) -> str:
    # Paragraph понимает мини-разметку — пользовательские данные экранируем
    return str(value if value is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pdf_table(columns: list[str], rows: list[list[Any]], styles: dict, col_widths=None) -> Table:
    data = [[Paragraph(_escape(col), styles["cell_bold"]) for col in columns]]
    data += [[Paragraph(_escape(cell), styles["cell"]) for cell in row] for row in rows]
    table = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _BRAND_COLOR),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F6FA")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B0BEC5")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _pdf_chart(chart: ReportChart) -> Drawing | None:
    if not chart.series or not chart.labels:
        return None
    regular, _ = _pdf_fonts()
    step = max(1, len(chart.labels) // _MAX_CHART_LABELS)
    labels = [label if index % step == 0 else "" for index, label in enumerate(chart.labels)]

    drawing = Drawing(170 * mm, 70 * mm)
    plot = HorizontalLineChart() if chart.chart_type == "line" else VerticalBarChart()
    plot.x, plot.y = 12 * mm, 12 * mm
    plot.width, plot.height = 150 * mm, 52 * mm
    plot.data = [tuple(series.values) for series in chart.series]
    plot.categoryAxis.categoryNames = labels
    plot.categoryAxis.labels.fontName = regular
    plot.categoryAxis.labels.fontSize = 6
    plot.categoryAxis.labels.angle = 30
    plot.categoryAxis.labels.boxAnchor = "ne"
    plot.valueAxis.labels.fontName = regular
    plot.valueAxis.labels.fontSize = 7
    plot.valueAxis.valueMin = 0
    palette = [_BRAND_COLOR, colors.HexColor("#2E8B57"), colors.HexColor("#E07B39")]
    for index in range(len(chart.series)):
        if chart.chart_type == "line":
            plot.lines[index].strokeColor = palette[index % len(palette)]
            plot.lines[index].strokeWidth = 1.5
        else:
            plot.bars[index].fillColor = palette[index % len(palette)]
    drawing.add(plot)
    return drawing


def render_pdf(report: ReportData) -> bytes:
    styles = _pdf_styles()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, title=report.title,
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
    )
    story: list = [
        Paragraph(_escape(report.title), styles["title"]),
        Paragraph(
            _escape(f"Период: {report.period} ({report.period_start:%d.%m.%Y} — {report.period_end:%d.%m.%Y}). "
                    f"Сформирован: {report.generated_at:%d.%m.%Y %H:%M} UTC"),
            styles["body"],
        ),
        Spacer(1, 6),
    ]
    if report.summary:
        story.append(_pdf_table(
            ["Показатель", "Значение"], [[item.label, item.value] for item in report.summary],
            styles, col_widths=[70 * mm, 110 * mm],
        ))
    if report.chart:
        drawing = _pdf_chart(report.chart)
        if drawing is not None:
            story += [Paragraph(_escape(report.chart.title), styles["heading"]), drawing]
    for table in report.tables:
        story.append(Paragraph(_escape(table.title), styles["heading"]))
        story.append(_pdf_table(table.columns, table.rows, styles) if table.rows
                     else Paragraph("Нет данных", styles["body"]))
    document.build(story)
    return buffer.getvalue()


# ── Excel ─────────────────────────────────────────────────────────────────────

def _xlsx_write_table(sheet, start_row: int, columns: list[str], rows: list[list[Any]]) -> int:
    for col_index, column in enumerate(columns, start=1):
        cell = sheet.cell(row=start_row, column=col_index, value=column)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(vertical="center")
    for row_offset, row in enumerate(rows, start=1):
        for col_index, value in enumerate(row, start=1):
            sheet.cell(row=start_row + row_offset, column=col_index, value=value)
    return start_row + len(rows) + 2


def _xlsx_autowidth(sheet) -> None:
    for column_cells in sheet.columns:
        width = max((len(str(cell.value)) for cell in column_cells if cell.value is not None), default=8)
        sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = min(max(width + 2, 10), 60)


def render_xlsx(report: ReportData) -> bytes:
    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Сводка"
    summary_sheet["A1"] = report.title
    summary_sheet["A1"].font = Font(bold=True, size=14)
    summary_sheet["A2"] = (
        f"Период: {report.period} ({report.period_start:%d.%m.%Y} — {report.period_end:%d.%m.%Y})"
    )
    _xlsx_write_table(summary_sheet, 4, ["Показатель", "Значение"],
                      [[item.label, item.value] for item in report.summary])
    _xlsx_autowidth(summary_sheet)

    for index, table in enumerate(report.tables, start=1):
        # Имя листа Excel: до 31 символа, без спецсимволов
        title = "".join(ch for ch in table.title if ch not in r"[]:*?/\\")[:28] or f"Таблица {index}"
        sheet = workbook.create_sheet(title)
        _xlsx_write_table(sheet, 1, table.columns, table.rows)
        _xlsx_autowidth(sheet)

    if report.chart and report.chart.labels:
        _xlsx_chart_sheet(workbook, report.chart)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _xlsx_chart_sheet(workbook: Workbook, chart_data: ReportChart) -> None:
    sheet = workbook.create_sheet("График")
    columns = ["Метка"] + [series.name for series in chart_data.series]
    rows = [
        [label] + [series.values[index] for series in chart_data.series]
        for index, label in enumerate(chart_data.labels)
    ]
    _xlsx_write_table(sheet, 1, columns, rows)

    chart_classes = {"line": LineChart, "bar": BarChart, "pie": PieChart}
    chart = chart_classes[chart_data.chart_type]()
    chart.title = chart_data.title
    data = Reference(sheet, min_col=2, max_col=len(columns), min_row=1, max_row=len(rows) + 1)
    categories = Reference(sheet, min_col=1, min_row=2, max_row=len(rows) + 1)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(categories)
    chart.width, chart.height = 22, 11
    sheet.add_chart(chart, f"{get_column_letter(len(columns) + 2)}2")
