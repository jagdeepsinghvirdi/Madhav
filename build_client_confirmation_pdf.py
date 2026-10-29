#!/usr/bin/env python3
"""Client confirmation PDF — only the items that need a Yes / No."""
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
	KeepTogether,
	Paragraph,
	SimpleDocTemplate,
	Spacer,
	Table,
	TableStyle,
)

OUT = Path(__file__).resolve().parent / "Delivery_Note_FWO_Client_Confirmation.pdf"

NAVY = colors.HexColor("#1B365D")
TEAL = colors.HexColor("#1F6F8B")
GOLD = colors.HexColor("#C4A35A")
GREEN = colors.HexColor("#1B7A4E")
AMBER = colors.HexColor("#B86E00")
RED = colors.HexColor("#9B2C2C")
LIGHT = colors.HexColor("#F4F7FA")
ROW_ALT = colors.HexColor("#EEF3F7")
LINE = colors.HexColor("#D5DEE7")
BODY = colors.HexColor("#243040")
MUTED = colors.HexColor("#5B6775")
OK_BG = colors.HexColor("#E8F5EE")
HOLD_BG = colors.HexColor("#FDECEC")
REC_BG = colors.HexColor("#EAF4F8")


def styles():
	base = getSampleStyleSheet()
	return {
		"kicker": ParagraphStyle(
			"kicker",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=9,
			textColor=TEAL,
			alignment=TA_CENTER,
			spaceAfter=6,
		),
		"title": ParagraphStyle(
			"title",
			parent=base["Title"],
			fontName="Helvetica-Bold",
			fontSize=18,
			leading=24,
			textColor=NAVY,
			alignment=TA_CENTER,
			spaceAfter=6,
		),
		"sub": ParagraphStyle(
			"sub",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=10.5,
			leading=14.5,
			textColor=MUTED,
			alignment=TA_CENTER,
			spaceAfter=4,
		),
		"h1": ParagraphStyle(
			"h1",
			parent=base["Heading1"],
			fontName="Helvetica-Bold",
			fontSize=12.5,
			leading=16,
			textColor=NAVY,
			spaceBefore=12,
			spaceAfter=7,
			keepWithNext=True,
		),
		"h2": ParagraphStyle(
			"h2",
			parent=base["Heading2"],
			fontName="Helvetica-Bold",
			fontSize=11,
			leading=14.5,
			textColor=TEAL,
			spaceBefore=8,
			spaceAfter=5,
			keepWithNext=True,
		),
		"body": ParagraphStyle(
			"body",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=9.5,
			leading=13.4,
			textColor=BODY,
			alignment=TA_JUSTIFY,
			spaceAfter=5,
		),
		"left": ParagraphStyle(
			"left",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=9.5,
			leading=13.4,
			textColor=BODY,
			alignment=TA_LEFT,
			spaceAfter=5,
		),
		"small": ParagraphStyle(
			"small",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=8.4,
			leading=11.8,
			textColor=BODY,
			alignment=TA_LEFT,
			spaceAfter=2,
		),
		"cell": ParagraphStyle(
			"cell",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=8.1,
			leading=11.4,
			textColor=BODY,
		),
		"cell_b": ParagraphStyle(
			"cell_b",
			parent=base["Normal"],
			fontName="Helvetica-Bold",
			fontSize=8.1,
			leading=11.4,
			textColor=NAVY,
		),
		"th": ParagraphStyle(
			"th",
			parent=base["Normal"],
			fontName="Helvetica-Bold",
			fontSize=8,
			leading=11,
			textColor=colors.white,
		),
		"note": ParagraphStyle(
			"note",
			parent=base["Normal"],
			fontName="Helvetica-Oblique",
			fontSize=8.4,
			leading=11.8,
			textColor=MUTED,
			spaceAfter=7,
		),
		"bullet": ParagraphStyle(
			"bullet",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=9.3,
			leading=13,
			textColor=BODY,
			leftIndent=14,
			firstLineIndent=-10,
			spaceAfter=2.5,
		),
		"opt": ParagraphStyle(
			"opt",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=9,
			leading=12.6,
			textColor=BODY,
			alignment=TA_LEFT,
		),
		"opt_b": ParagraphStyle(
			"opt_b",
			parent=base["Normal"],
			fontName="Helvetica-Bold",
			fontSize=9,
			leading=12.6,
			textColor=NAVY,
		),
		"tick": ParagraphStyle(
			"tick",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=9,
			leading=13,
			textColor=BODY,
			leftIndent=8,
			spaceAfter=3,
		),
	}


def header_footer(canvas, doc):
	canvas.saveState()
	w, h = A4
	canvas.setFillColor(NAVY)
	canvas.rect(0, h - 12 * mm, w, 12 * mm, fill=1, stroke=0)
	canvas.setFillColor(colors.white)
	canvas.setFont("Helvetica", 8)
	canvas.drawString(18 * mm, h - 7.5 * mm, "Madhav  |  Delivery Note & Finish Work Order")
	canvas.drawRightString(w - 18 * mm, h - 7.5 * mm, "Client confirmation")
	canvas.setFillColor(GOLD)
	canvas.rect(0, h - 13.2 * mm, w, 1.2 * mm, fill=1, stroke=0)
	canvas.setFillColor(LIGHT)
	canvas.rect(0, 0, w, 12 * mm, fill=1, stroke=0)
	canvas.setFillColor(MUTED)
	canvas.setFont("Helvetica", 8)
	canvas.drawString(18 * mm, 5 * mm, "Please mark one option per decision and return this document")
	canvas.drawRightString(w - 18 * mm, 5 * mm, f"Page {doc.page}")
	canvas.restoreState()


def P(text, style):
	return Paragraph(text, style)


def bullets(items, style):
	flow = [Paragraph(f"•  {item}", style) for item in items]
	flow.append(Spacer(1, 1.5 * mm))
	return flow


def table(headers, rows, col_widths, row_colors=None):
	s = styles()
	data = [[P(h, s["th"]) for h in headers]]
	data.extend(rows)
	t = Table(data, colWidths=col_widths, repeatRows=1)
	cmds = [
		("BACKGROUND", (0, 0), (-1, 0), NAVY),
		("VALIGN", (0, 0), (-1, -1), "TOP"),
		("LEFTPADDING", (0, 0), (-1, -1), 6),
		("RIGHTPADDING", (0, 0), (-1, -1), 6),
		("TOPPADDING", (0, 0), (-1, -1), 5),
		("BOTTOMPADDING", (0, 0), (-1, -1), 5),
		("GRID", (0, 0), (-1, -1), 0.3, LINE),
		("BACKGROUND", (0, 1), (-1, -1), colors.white),
	]
	for i in range(1, len(data)):
		bg = (row_colors or {}).get(i)
		if bg:
			cmds.append(("BACKGROUND", (0, i), (-1, i), bg))
		elif i % 2 == 0:
			cmds.append(("BACKGROUND", (0, i), (-1, i), ROW_ALT))
	t.setStyle(TableStyle(cmds))
	return t


def box(title, body, s, border, bg=LIGHT):
	inner = [P(f"<b>{title}</b>", s["cell_b"]), Spacer(1, 1.6 * mm), P(body, s["small"])]
	t = Table([[inner]], colWidths=[174 * mm])
	t.setStyle(
		TableStyle(
			[
				("BACKGROUND", (0, 0), (-1, -1), bg),
				("BOX", (0, 0), (-1, -1), 1.15, border),
				("LEFTPADDING", (0, 0), (-1, -1), 9),
				("RIGHTPADDING", (0, 0), (-1, -1), 9),
				("TOPPADDING", (0, 0), (-1, -1), 7),
				("BOTTOMPADDING", (0, 0), (-1, -1), 7),
			]
		)
	)
	return t


def option_table(s, rec_label, rec_text, alt_label, alt_text):
	rows = [
		[
			P("Option A — recommended", s["cell_b"]),
			P(rec_label, s["cell_b"]),
			P(rec_text, s["cell"]),
		],
		[
			P("Option B — as in your issues paper", s["cell_b"]),
			P(alt_label, s["cell_b"]),
			P(alt_text, s["cell"]),
		],
	]
	t = Table(
		[[P("Choice", s["th"]), P("Meaning", s["th"]), P("What happens", s["th"])]] + rows,
		colWidths=[48 * mm, 38 * mm, 88 * mm],
	)
	t.setStyle(
		TableStyle(
			[
				("BACKGROUND", (0, 0), (-1, 0), NAVY),
				("BACKGROUND", (0, 1), (-1, 1), OK_BG),
				("BACKGROUND", (0, 2), (-1, 2), HOLD_BG),
				("VALIGN", (0, 0), (-1, -1), "TOP"),
				("LEFTPADDING", (0, 0), (-1, -1), 6),
				("RIGHTPADDING", (0, 0), (-1, -1), 6),
				("TOPPADDING", (0, 0), (-1, -1), 5),
				("BOTTOMPADDING", (0, 0), (-1, -1), 5),
				("GRID", (0, 0), (-1, -1), 0.3, LINE),
			]
		)
	)
	return t


def ticks(s, a, b):
	return [
		P(f"[  ]  <b>Option A</b> — {a}", s["tick"]),
		P(f"[  ]  <b>Option B</b> — {b}", s["tick"]),
		P("[  ]  <b>Other</b> (write below) _______________________________________________", s["tick"]),
		Spacer(1, 2 * mm),
	]


def decision_block(story, s, code, title, asked, today, rec, alt, risks, example, tick_a, tick_b):
	block = []
	block.append(P(f"{code}.  {title}", s["h1"]))
	block.append(P(f"<b>Your request:</b> {asked}", s["body"]))
	block.append(P(f"<b>What the system does today:</b> {today}", s["body"]))
	block.append(Spacer(1, 1.5 * mm))
	block.append(option_table(s, rec[0], rec[1], alt[0], alt[1]))
	block.append(Spacer(1, 2.5 * mm))
	block.append(P("<b>If we implement Option B, these issues can appear:</b>", s["left"]))
	block.extend(bullets(risks, s["bullet"]))
	block.append(
		box("Worked example", example, s, AMBER, bg=colors.HexColor("#FFF8EE"))
	)
	block.append(Spacer(1, 2.5 * mm))
	block.append(P("<b>Please tick one:</b>", s["left"]))
	block.extend(ticks(s, tick_a, tick_b))
	story.append(KeepTogether(block))


def build():
	s = styles()
	story = []

	story.append(Spacer(1, 4 * mm))
	story.append(P("FOR CLIENT CONFIRMATION ONLY", s["kicker"]))
	story.append(P("Delivery Note &amp; Finish Work Order<br/>Items that need your Yes / No", s["title"]))
	story.append(
		P(
			"Response to: <i>Current Issues in Delivery Note &amp; FWO</i><br/>"
			f"{date.today().strftime('%d %B %Y')}  ·  Five decisions. Nothing in this paper has been implemented.",
			s["sub"],
		)
	)
	story.append(Spacer(1, 3 * mm))
	story.append(
		box(
			"Please read this first",
			"This document covers <b>only</b> the requests we have <b>not</b> built, because doing them as written "
			"would change current Sales or Production behaviour. Your issues paper also said existing functionality "
			"must not break.<br/><br/>"
			"The Pcs 21 vs 22 fix, the reservation timeline log, and live Finish Work Order totals are <b>not</b> in this paper — "
			"those can go live without this confirmation.<br/><br/>"
			"For each decision below, tick <b>Option A</b> (keep today’s safe behaviour) or <b>Option B</b> (do what the issues paper asked). "
			"Until this sheet comes back marked, these five items stay as they are today.",
			s,
			TEAL,
		)
	)

	story.append(P("Summary of the five decisions", s["h1"]))
	story.append(
		table(
			["#", "Topic", "Option A (recommended)", "Option B (your paper)"],
			[
				[
					P("D1", s["cell_b"]),
					P("Reserved Delivery Note <b>Qty</b>", s["cell"]),
					P("Keep Qty = reserved weight. Formula stays off on reserved rows.", s["cell"]),
					P("Auto Qty = (Pcs × Length × Section Weight) / 1000 when Pcs changes, using Sales Order values.", s["cell"]),
				],
				[
					P("D2", s["cell_b"]),
					P("When Pcs is capped 22 → 21, also cut <b>Qty (tonnes)</b>?", s["cell"]),
					P("No. Pcs becomes 21. Reserved tonnes stay as reserved.", s["cell"]),
					P("Yes. Shrink reserved Qty to 21 bars of weight as well.", s["cell"]),
				],
				[
					P("D3", s["cell_b"]),
					P("Delivery Note <b>Length</b>", s["cell"]),
					P("Show actual batch / mill length. Sales Order length stays as reference only.", s["cell"]),
					P("Force Sales Order ordered length onto the Delivery Note and into any Qty formula.", s["cell"]),
				],
				[
					P("D4", s["cell_b"]),
					P("Finish Work Order Save <b>errors</b>", s["cell"]),
					P("Keep current: checks already run on Save; stop at the first error.", s["cell"]),
					P("Show every Save error in one list. Must still skip Fetch auto-save.", s["cell"]),
				],
				[
					P("D5", s["cell_b"]),
					P("Reservation <b>audit</b> format", s["cell"]),
					P("Timeline comment on the Stock Reservation Entry is enough (already built).", s["cell"]),
					P("Also add extra form fields and / or a printed who-did-what report.", s["cell"]),
				],
			],
			[14 * mm, 42 * mm, 59 * mm, 59 * mm],
			row_colors={1: OK_BG, 2: OK_BG, 3: OK_BG, 4: OK_BG, 5: REC_BG},
		)
	)
	story.append(
		P(
			"Green rows: we recommend Option A. D5 (blue) is already built as Option A — tick Option B only if you also want extra fields or a report.",
			s["note"],
		)
	)

	# D1
	decision_block(
		story,
		s,
		"D1",
		"Automatic Qty on reserved Delivery Notes",
		"Calculate Qty automatically as (Pcs × Length × Section Weight) / 1000, using Pcs, Length and Section Weight "
		"from the Sales Order. Recalculate only when Pcs changes, in real time, with no manual step.",
		"That formula already runs on Delivery Notes that are <b>not</b> reserved. On reserved Sales Delivery Notes it is "
		"<b>switched off on purpose</b>. Qty is the reserved weight from the Stock Reservation Entry. "
		"We had to switch the formula off earlier because it rewrote reserved qty from <b>0.445 to 0.443</b> and the "
		"stock ledger no longer matched the reservation. Length on the row is the batch length; Sales Order length is "
		"shown only as a reference field.",
		(
			"Keep Qty as reserved weight",
			"Reserved Delivery Notes do not recalc Qty from Pcs. Stock, remaining reserved qty, and cancel-restore stay in line with the reservation.",
		),
		(
			"Turn the formula on for reserved rows",
			"Every Pcs change (including Get Items From Sales Order) rewrites Qty from the formula, using Sales Order length and section weight if we follow the paper literally.",
		),
		[
			"<b>Reserved Qty will no longer match the Stock Reservation Entry.</b> Delivery, leftover reserved stock, and Delivery Note cancel-restore all use that weight.",
			"<b>Sales Order length is often not the mill length</b> (example: SO 6.00 m, batch 6.50 m). Using SO length in the formula changes tonnes versus the physical bars.",
			"<b>Section Weight on the Sales Order / Item can differ from the batch.</b> Mixing them desyncs the Delivery Note row from the Serial and Batch Bundle.",
			"<b>Deliver-as-Qty / Invoice Qty</b> can fail the 3% tolerance, or create extra Stock Reconciliations, if physical Qty is rewritten from Pcs.",
			"<b>“Only when Pcs changes” still moves Qty.</b> Get Items stamps Pcs and Qty together. That looks like a Pcs change and would fire the formula on every fetch.",
			"If Option B is approved, we still need a written rule: which Length (SO vs batch) and whether Invoice Qty or stock Qty should move. Without that, we cannot build it safely.",
		],
		"A reservation holds <b>0.445 t</b> for <b>21</b> bars of about 6.5 m. The Sales Order length is <b>6.0 m</b>. "
		"Option A keeps Delivery Note Qty at <b>0.445 t</b> and Pcs at 21. "
		"Option B using SO length would rewrite Qty to 21 × 6.0 × section weight / 1000 — a different tonne figure — "
		"then stock posting and the reservation would no longer agree. That is the 0.445 → 0.443 class of problem, only larger.",
		"Keep reserved Qty as reserved weight. Formula stays off on reserved / Deliver-as-Qty rows.",
		"Turn on auto Qty for reserved Delivery Notes. We will confirm Length source with you before coding.",
	)

	# D2
	decision_block(
		story,
		s,
		"D2",
		"When Pcs is capped from 22 to 21, should Qty (tonnes) also be reduced?",
		"While fixing the Pcs mismatch (FWO 21, Batch 21, reservation 22), also check Qty and Length.",
		"The Pcs fix (already built, not part of this confirmation) caps Pcs to the Batch (21). "
		"<b>Reserved Qty is not reduced.</b> You can therefore see 21 Pcs next to a weight that was calculated when the "
		"system thought there were 22 bars. The gap is usually a few kilograms. Length stays the batch / reservation length.",
		(
			"Cap Pcs only",
			"Delivery Note shows 21 Pcs. Tonnes stay the original reserved weight. Sales Order remaining qty and restore snapshots do not move.",
		),
		(
			"Also shrink Qty",
			"Recalculate reserved / Delivery Note Qty down to 21 × Length × Section Weight / 1000, so Pcs and tonnes “match” on paper.",
		),
		[
			"<b>Sales Order remaining reserved qty changes.</b> Finish Work Order, Batch Wise Reservation Tool and Stock Transfer share that remaining-qty pool. Their allowance can disagree with what was already reserved.",
			"<b>Delivery Note cancel-restore</b> stores the reserved qty in a snapshot. After cancel, the restored reservation would not match the original reserve.",
			"A few kilograms of mill weight that was actually produced could be released and look “unreserved” on the Sales Order, so another user could reserve it again.",
			"If Length in that formula is Sales Order length (see D3), the tonne cut is even larger than one extra bar.",
		],
		"FWO produced <b>21 bars</b>. Reservation stored <b>22 Pcs</b> and, say, <b>0.460 t</b>. "
		"Option A: Delivery Note Pcs = 21, Qty = 0.460 t. The extra ~one-bar of weight stays reserved; Save no longer errors. "
		"Option B: Qty is cut to 21 bars of weight (for example 0.438 t). The Sales Order suddenly shows ~0.022 t still free. "
		"A second reservation or a Batch Wise Reservation Tool row can then take that leftover, while the mill only made 21 bars.",
		"Do not reduce reserved Qty. Accept a small Pcs vs tonnes difference on those rows.",
		"Also reduce reserved Qty to 21 bars of weight. Accept remaining-qty and restore side effects.",
	)

	# D3
	decision_block(
		story,
		s,
		"D3",
		"Which Length should the Delivery Note use?",
		"Use Length fetched from the Sales Order for the Delivery Note, including any automatic Qty calculation.",
		"Sales Order ordered length is stored on the Delivery Note as a <b>reference only</b> (length as per SO). "
		"The live Length on the row is the <b>actual reserved batch / mill length</b>. "
		"Reservation already allows mill length inside the Sales Order window (ordered length ± 2 m). "
		"Dispatch, Stock Reconciliation and piece-weight therefore follow the bar that was actually reserved.",
		(
			"Keep batch / mill length",
			"Delivery Note Length = what is in the warehouse. SO length remains visible as reference. Dispatch papers match the batch.",
		),
		(
			"Force Sales Order length",
			"Overwrite Delivery Note Length with the ordered length, even when the reserved batch is longer or shorter (within the allowed window).",
		),
		[
			"A 6.50 m mill bar reserved against a 6.00 m order would <b>print and calculate as 6.00 m</b>. Weighment and dispatch would not match the batch.",
			"Stock Reconciliation and reports that read Delivery Note length would show ordered length, not mill length.",
			"If D1 Option B is also chosen, using SO length in the Qty formula changes tonnes for every reserved Delivery Note, not only the 21 vs 22 cases.",
			"The length window on reservation (± 2 m) would become display-only. Users would think the mill supplied the ordered length when it did not.",
		],
		"Sales Order: <b>6.00 m</b>. Mill batch reserved: <b>6.50 m</b> (inside ± 2 m, so reservation is valid). "
		"Option A: Delivery Note shows 6.50 m; SO 6.00 m stays in the reference column. "
		"Option B: Delivery Note shows 6.00 m for a 6.50 m bar. Qty and any auto-formula then understate the physical tonnes.",
		"Keep showing actual batch / mill length. SO length stays reference only.",
		"Force Sales Order ordered length onto the Delivery Note.",
	)

	# D4
	decision_block(
		story,
		s,
		"D4",
		"Finish Work Order — show every Save error at once?",
		"On Save, run all validations and show every error at once, not at submission.",
		"The stock, raw-material, yield, length and batch checks <b>already run on manual Save</b>, not only on Submit. "
		"Submit repeats them as a safety net, then posts Stock Entry and reservation. "
		"Messages still stop at the <b>first</b> error (except insufficient batch stock, which is already collected). "
		"The first Fetch on a new Finish Work Order silent-saves <b>without</b> those business errors, so the Quality Inspection warehouse can be stored while Raw Materials is still empty.",
		(
			"Keep first-error on Save",
			"No behaviour change. Fetch stays silent. User still sees RM / yield / stock errors on their own Save click, before Submit.",
		),
		(
			"Collect every error into one list",
			"One Save shows all problems together. Fetch auto-save must remain exempt, or a new Finish Work Order will fail Fetch with “No Raw Materials”.",
		),
		[
			"<b>If Fetch is not kept exempt,</b> the first Fetch on a new Finish Work Order will throw “No Raw Materials to consume”, because Fetch clears the RM table on purpose.",
			"Users will see <b>overlapping messages</b> on the same empty RM table (for example “raw material required” and “pool exhausted”). That can look like two different faults.",
			"Error <b>order changes</b>. Today a missing batch is shown before yield. A full list is noisier; it does not catch new classes of error that Save does not already catch.",
			"This is a screen/message change only. It does not post stock earlier, and it does not replace Submit (Stock Entry and reservation still happen on Submit).",
		],
		"User Fetches 4 work orders, then clicks Save with no raw material yet. "
		"Today: one message, e.g. “No Raw Materials to consume”. "
		"Option B: several messages at once — no RM, yield short, pool exhausted, finished qty checks — all describing the same empty RM table. "
		"If Fetch itself ran those checks, Fetch would fail before the user could even add RM.",
		"Keep current Save behaviour (first error). Treat “validations on Save not Submit” as already met.",
		"Show all Save errors in one list. Fetch auto-save must stay without business errors.",
	)

	# D5
	decision_block(
		story,
		s,
		"D5",
		"Stock Reservation audit — timeline only, or extra fields / report?",
		"Log who created, edited or cancelled each Stock Reservation Entry (manually or via Sales Order), with date and time.",
		"This log is <b>already built</b> as a timeline comment on the Stock Reservation Entry: action (Created / Edited / Cancelled), user, date and time, and source "
		"(Finish Work Order, Batch Wise Reservation Tool, Stock Transfer, Sales Order, or Manual). "
		"Edited is logged only when reserved qty itself changes after submit — not when a Delivery Note merely delivers against it. "
		"There are no extra columns on the form and no separate printed report.",
		(
			"Timeline is enough",
			"No extra work. Open the Stock Reservation Entry → timeline. Old reservations created before go-live will not have a back-filled create comment.",
		),
		(
			"Also add fields and / or a report",
			"Custom fields on the form (Created By / Cancelled By / Source) and optionally a list report you can export.",
		),
		[
			"Option B is extra build, not a functional risk to Sales or Production. It needs a field layout and, if you want a report, the columns you need (date range, Sales Order, user, source).",
			"A Link field cannot point at a cancelled reservation from the Delivery Note after some Deliver-as-Qty cancels. A report must read the reservation’s own timeline / fields, not the Delivery Note.",
			"If you only need “who cancelled this?”, the timeline already shows it. A report is useful if stores want a weekly list.",
		],
		"User A reserves from a Sales Order at 10:12. User B cancels it from a Delivery Note at 16:40. "
		"Option A: on that Stock Reservation Entry timeline you see “Created by A via Sales Order …” and “Cancelled by B via Sales Order …”. "
		"Option B: the same facts also appear in named fields and/or an Excel report of all reservations for the day.",
		"Timeline comment is enough. No extra fields or report.",
		"Please also add form fields and / or a report. We will confirm the column list with you.",
	)

	empty = "[  ] A&nbsp;&nbsp;&nbsp;[  ] B&nbsp;&nbsp;&nbsp;[  ] Other"
	sign = table(
		["", "Name", "Role", "Date", "Signature"],
		[
			[
				P("<b>Confirmed by (client)</b>", s["cell_b"]),
				P("<br/><br/>", s["cell"]),
				P(" ", s["cell"]),
				P(" ", s["cell"]),
				P(" ", s["cell"]),
			],
			[
				P("<b>Noted by (Madhav / Finbyz)</b>", s["cell_b"]),
				P("<br/><br/>", s["cell"]),
				P(" ", s["cell"]),
				P(" ", s["cell"]),
				P(" ", s["cell"]),
			],
		],
		[42 * mm, 33 * mm, 33 * mm, 28 * mm, 38 * mm],
	)
	reply = [
		P("Reply sheet — sign and return", s["h1"]),
		P(
			"Please tick one option per row. If a row is left blank, we will treat it as Option A.",
			s["body"],
		),
		table(
			["#", "Decision", "Your choice", "If Other, write the rule"],
			[
				[
					P("D1", s["cell_b"]),
					P("Reserved DN Qty = reserved weight, or auto formula?", s["cell"]),
					P(empty, s["cell"]),
					P("Length for formula: SO / batch / ________", s["cell"]),
				],
				[
					P("D2", s["cell_b"]),
					P("When Pcs 22 → 21, also cut reserved Qty?", s["cell"]),
					P(empty, s["cell"]),
					P(" ", s["cell"]),
				],
				[
					P("D3", s["cell_b"]),
					P("DN Length = mill batch, or Sales Order length?", s["cell"]),
					P(empty, s["cell"]),
					P(" ", s["cell"]),
				],
				[
					P("D4", s["cell_b"]),
					P("FWO Save: first error, or all errors at once?", s["cell"]),
					P(empty, s["cell"]),
					P("Fetch silent-save stays exempt: Yes / No", s["cell"]),
				],
				[
					P("D5", s["cell_b"]),
					P("Reservation audit: timeline only, or fields / report?", s["cell"]),
					P(empty, s["cell"]),
					P("If report, columns: ________", s["cell"]),
				],
			],
			[12 * mm, 58 * mm, 40 * mm, 64 * mm],
		),
		Spacer(1, 5 * mm),
		sign,
		Spacer(1, 4 * mm),
		box(
			"What happens after you return this",
			"Option A on D1–D4 means we leave those behaviours as they are today. Option B on any of D1–D4 is a separate, tested change — not mixed into the Pcs / timeline / live-total release. "
			"D5 Option A is already built. D5 Option B is extra design (fields / report) after you list the columns.",
			s,
			GREEN,
			bg=OK_BG,
		),
		Spacer(1, 2 * mm),
		P(
			"This sheet does not authorise Option B until it is ticked and signed. It is for confirmation only.",
			s["note"],
		),
	]
	story.append(KeepTogether(reply))

	doc = SimpleDocTemplate(
		str(OUT),
		pagesize=A4,
		leftMargin=18 * mm,
		rightMargin=18 * mm,
		topMargin=20 * mm,
		bottomMargin=16 * mm,
		title="Delivery Note & FWO — Client confirmation",
		author="Madhav",
	)
	doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
	print(f"Wrote {OUT}")


if __name__ == "__main__":
	build()
