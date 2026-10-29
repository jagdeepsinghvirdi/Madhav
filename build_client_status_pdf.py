#!/usr/bin/env python3
"""Build the client-facing DN & FWO implementation note PDF."""
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
	Paragraph,
	SimpleDocTemplate,
	Spacer,
	Table,
	TableStyle,
)

OUT = Path(__file__).resolve().parent / "Delivery_Note_FWO_Implementation_Note.pdf"

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


def styles():
	base = getSampleStyleSheet()
	s = {
		"cover_kicker": ParagraphStyle(
			"cover_kicker",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=9,
			textColor=TEAL,
			alignment=TA_CENTER,
			tracking=1.2,
			spaceAfter=6,
		),
		"cover_title": ParagraphStyle(
			"cover_title",
			parent=base["Title"],
			fontName="Helvetica-Bold",
			fontSize=20,
			leading=26,
			textColor=NAVY,
			alignment=TA_CENTER,
			spaceAfter=8,
		),
		"cover_sub": ParagraphStyle(
			"cover_sub",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=11,
			leading=15,
			textColor=MUTED,
			alignment=TA_CENTER,
			spaceAfter=4,
		),
		"h1": ParagraphStyle(
			"h1",
			parent=base["Heading1"],
			fontName="Helvetica-Bold",
			fontSize=13,
			leading=17,
			textColor=NAVY,
			spaceBefore=14,
			spaceAfter=8,
			borderPadding=0,
			keepWithNext=True,
		),
		"h2": ParagraphStyle(
			"h2",
			parent=base["Heading2"],
			fontName="Helvetica-Bold",
			fontSize=11.5,
			leading=15,
			textColor=TEAL,
			spaceBefore=10,
			spaceAfter=6,
			keepWithNext=True,
		),
		"body": ParagraphStyle(
			"body",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=9.5,
			leading=13.5,
			textColor=BODY,
			alignment=TA_JUSTIFY,
			spaceAfter=6,
		),
		"body_left": ParagraphStyle(
			"body_left",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=9.5,
			leading=13.5,
			textColor=BODY,
			alignment=TA_LEFT,
			spaceAfter=6,
		),
		"small": ParagraphStyle(
			"small",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=8.5,
			leading=12,
			textColor=BODY,
			alignment=TA_LEFT,
			spaceAfter=3,
		),
		"cell": ParagraphStyle(
			"cell",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=8.2,
			leading=11.5,
			textColor=BODY,
		),
		"cell_b": ParagraphStyle(
			"cell_b",
			parent=base["Normal"],
			fontName="Helvetica-Bold",
			fontSize=8.2,
			leading=11.5,
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
			fontSize=8.5,
			leading=12,
			textColor=MUTED,
			spaceAfter=8,
			spaceBefore=2,
		),
		"footer": ParagraphStyle(
			"footer",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=8,
			textColor=MUTED,
			alignment=TA_CENTER,
		),
		"bullet": ParagraphStyle(
			"bullet",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=9.5,
			leading=13.2,
			textColor=BODY,
			leftIndent=14,
			firstLineIndent=-10,
			spaceAfter=3,
		),
		"decision": ParagraphStyle(
			"decision",
			parent=base["Normal"],
			fontName="Helvetica",
			fontSize=9.5,
			leading=13.5,
			textColor=BODY,
			leftIndent=14,
			spaceAfter=7,
		),
	}
	return s


def header_footer(canvas, doc):
	canvas.saveState()
	w, h = A4
	canvas.setFillColor(NAVY)
	canvas.rect(0, h - 12 * mm, w, 12 * mm, fill=1, stroke=0)
	canvas.setFillColor(colors.white)
	canvas.setFont("Helvetica", 8)
	canvas.drawString(18 * mm, h - 7.5 * mm, "Madhav  |  Delivery Note & Finish Work Order")
	canvas.drawRightString(w - 18 * mm, h - 7.5 * mm, "Implementation Note")
	canvas.setFillColor(GOLD)
	canvas.rect(0, h - 13.2 * mm, w, 1.2 * mm, fill=1, stroke=0)

	canvas.setFillColor(LIGHT)
	canvas.rect(0, 0, w, 12 * mm, fill=1, stroke=0)
	canvas.setFillColor(MUTED)
	canvas.setFont("Helvetica", 8)
	canvas.drawString(18 * mm, 5 * mm, "Confidential  —  for client review")
	canvas.drawRightString(w - 18 * mm, 5 * mm, f"Page {doc.page}")
	canvas.restoreState()


def P(text, style):
	return Paragraph(text, style)


def bullets(items, style):
	flow = []
	for item in items:
		flow.append(Paragraph(f"•  {item}", style))
	flow.append(Spacer(1, 2 * mm))
	return flow


def status_chip(label, kind, s):
	colour = {
		"done": "#1B7A4E",
		"partial": "#B86E00",
		"hold": "#9B2C2C",
		"info": "#1F6F8B",
	}[kind]
	return P(f'<font color="{colour}"><b>{label}</b></font>', s["cell_b"])


def table(headers, rows, col_widths):
	s = styles()
	head = [P(h, s["th"]) for h in headers]
	data = [head]
	for row in rows:
		data.append(row)
	t = Table(data, colWidths=col_widths, repeatRows=1)
	style_cmds = [
		("BACKGROUND", (0, 0), (-1, 0), NAVY),
		("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
		("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
		("FONTSIZE", (0, 0), (-1, -1), 8),
		("VALIGN", (0, 0), (-1, -1), "TOP"),
		("LEFTPADDING", (0, 0), (-1, -1), 6),
		("RIGHTPADDING", (0, 0), (-1, -1), 6),
		("TOPPADDING", (0, 0), (-1, -1), 5),
		("BOTTOMPADDING", (0, 0), (-1, -1), 5),
		("GRID", (0, 0), (-1, -1), 0.3, LINE),
		("BACKGROUND", (0, 1), (-1, -1), colors.white),
	]
	for i in range(1, len(data)):
		if i % 2 == 0:
			style_cmds.append(("BACKGROUND", (0, i), (-1, i), ROW_ALT))
	t.setStyle(TableStyle(style_cmds))
	return t


def callout(title, body, s, border=TEAL):
	inner = [
		P(f"<b>{title}</b>", s["cell_b"]),
		Spacer(1, 2 * mm),
		P(body, s["small"]),
	]
	t = Table([[inner]], colWidths=[174 * mm])
	t.setStyle(
		TableStyle(
			[
				("BACKGROUND", (0, 0), (-1, -1), LIGHT),
				("BOX", (0, 0), (-1, -1), 1.2, border),
				("LEFTPADDING", (0, 0), (-1, -1), 10),
				("RIGHTPADDING", (0, 0), (-1, -1), 10),
				("TOPPADDING", (0, 0), (-1, -1), 8),
				("BOTTOMPADDING", (0, 0), (-1, -1), 8),
			]
		)
	)
	return t


def build():
	s = styles()
	story = []

	story.append(Spacer(1, 6 * mm))
	story.append(P("CLIENT REVIEW DOCUMENT", s["cover_kicker"]))
	story.append(P("Delivery Note &amp; Finish Work Order<br/>Implementation Note", s["cover_title"]))
	story.append(
		P(
			"Response to: <i>Current Issues in Delivery Note &amp; FWO</i><br/>"
			f"Date: {date.today().strftime('%d %B %Y')} &nbsp;|&nbsp; Status of requested changes, what went live, and what needs your confirmation",
			s["cover_sub"],
		)
	)
	story.append(Spacer(1, 4 * mm))
	story.append(
		callout(
			"How to read this note",
			"Your issues paper asked for three things, and also said existing Sales and Production "
			"must not break. We implemented only the parts that can go live without changing reserved "
			"stock weight, Sales Order length, or Finish Work Order save/submit rules. "
			"The remaining items are listed in Section 3 with the exact risk, so you can confirm Yes or No.",
			s,
			border=TEAL,
		)
	)

	# 1. Status
	story.append(P("1. Status at a glance", s["h1"]))
	story.append(
		P(
			"This table maps every line in your issues paper to what we have done. "
			"<b>Done</b> is already in the code. <b>Needs your confirmation</b> was not implemented, "
			"because doing it as written would change current Sales or Production behaviour.",
			s["body"],
		)
	)

	story.append(
		table(
			["#", "Your request", "Status", "What this means"],
			[
				[
					P("1", s["cell"]),
					P("Delivery Note: auto Qty = (Pcs × Length × Section Weight) / 1000, using Sales Order values, only when Pcs changes", s["cell"]),
					status_chip("Needs your confirmation", "hold", s),
					P("Formula already exists, but is switched <b>off</b> on reserved Sales Delivery Notes. Turning it on rewrites reserved tonnes and has previously drifted 0.445 → 0.443. See Section 3.1.", s["cell"]),
				],
				[
					P("1.1", s["cell"]),
					P("Pcs mismatch: FWO produces 21, Batch shows 21, reservation stores 22, Delivery Note fetches 22 and errors", s["cell"]),
					status_chip("Done", "done", s),
					P("New reservations and Delivery Note fetch now cap Pcs to the Batch piece count (21). Reserved <b>weight (Qty)</b> is not reduced. See Section 2.1.", s["cell"]),
				],
				[
					P("1.1", s["cell"]),
					P("Also check and align Qty and Length with the Batch", s["cell"]),
					status_chip("Needs your confirmation", "hold", s),
					P("We did not change reserved Qty or switch Delivery Note Length to Sales Order Length. Both would move tonnes / mill length. See Section 3.2.", s["cell"]),
				],
				[
					P("1.1", s["cell"]),
					P("Log who created, edited or cancelled each Stock Reservation Entry (manual or via Sales Order), with date and time", s["cell"]),
					status_chip("Done", "done", s),
					P("Timeline comment on the Stock Reservation Entry: user, date/time, and source (Sales Order / Finish Work Order / Batch Wise Reservation Tool / Manual). See Section 2.2.", s["cell"]),
				],
				[
					P("2", s["cell"]),
					P("Finish Work Order: recalculate Total Work Order Qty in real time when quantities change", s["cell"]),
					status_chip("Done", "done", s),
					P("Header Total Work Order Qty / Pieces now update as soon as Actual Ready Qty or Ready Pieces change, and after Fetch. Same formula as Save. See Section 2.3.", s["cell"]),
				],
				[
					P("2", s["cell"]),
					P("On Save, run all validations and show every error at once (not at Submit)", s["cell"]),
					status_chip("Needs your confirmation", "hold", s),
					P("Business checks already run on <b>Save</b> today. They still stop at the first error. Collecting every error at once would change Fetch auto-save and the message order. See Section 3.3.", s["cell"]),
				],
			],
			[12 * mm, 48 * mm, 38 * mm, 76 * mm],
		)
	)
	story.append(
		P(
			"Compatibility line from your paper — <i>“These changes must not break or alter any existing Sales or Production functionality”</i> — is the reason some items are held for confirmation.",
			s["note"],
		)
	)

	# 3. Done
	story.append(P("2. What is done — and what can go wrong with it", s["h1"]))
	story.append(
		P(
			"This section is for the items already implemented. Please read the “what can go wrong” "
			"paragraphs as well as the fix. Nothing here changes reserved tonnes, Sales Order remaining "
			"qty, or Finish Work Order yield.",
			s["body"],
		)
	)

	story.append(P("2.1 Pcs mismatch (Batch 21 vs reservation 22)", s["h2"]))
	story.append(
		P(
			"<b>What was going wrong.</b> Finish Work Order posts 21 pieces onto the Batch. "
			"When stock is reserved against the Sales Order, the system sometimes <i>ceils</i> the reserved "
			"weight up by one bar (21 bars of weight → 22 pieces). The Delivery Note then loads 22, "
			"and Save/Submit fails because the Batch only has 21.",
			s["body"],
		)
	)
	story.append(P("<b>What we changed.</b>", s["body_left"]))
	story.extend(
		bullets(
			[
				"When a Stock Reservation Entry is created or validated (Sales Order Reserve, and as a safety net on other reservation paths), <b>Pcs is capped to the Batch piece count</b>. In your example this stores 21, not 22.",
				"When a Delivery Note is made from the Sales Order, fetched Pcs is capped the same way. Old reservations that already hold 22 will show <b>21 on the new Delivery Note</b>.",
				"<b>Reserved Qty (tonnes) is not reduced.</b> The reservation still holds the original reserved weight. Piece count and weight stay on the existing reserved-stock model.",
			],
			s["bullet"],
		)
	)
	story.append(P("<b>What you should see after go-live.</b>", s["body_left"]))
	story.extend(
		bullets(
			[
				"New Finish Work Order → reservation → Delivery Note: Pcs on the Delivery Note match the Batch (21 / 21).",
				"Get Items From → Sales Order on an older reservation that still has 22 Pcs: Delivery Note loads 21. The error “Batch has only 21” should not appear for this case.",
				"If a user types more Pcs than the Batch holds on a reserved Delivery Note, that is still a hard error. We did not silently cap a user over-pick on reserved rows.",
			],
			s["bullet"],
		)
	)
	story.append(
		callout(
			"Possible issues from this change (please be aware)",
			"<b>1. Draft Delivery Notes already sitting with 22 Pcs</b> are not rewritten automatically. "
			"The user must use Get Items From → Sales Order again, or change Pcs to 21. Until then, Save can still show the old error.<br/><br/>"
			"<b>2. Qty (tonnes) may not equal 21 × Length × Section Weight / 1000.</b> "
			"That is intentional. Reserved Delivery Note Qty stays the reservation weight so stock ledger, "
			"remaining reserved qty, and cancel-restore do not move. You may see 21 Pcs next to a weight that was calculated when the system thought there were 22. "
			"The gap is usually a few kilograms. If you want that weight reduced as well, that is <b>D2 in Section 4</b>.<br/><br/>"
			"<b>3. Cap uses Batch Pcs.</b> If the Batch piece count is blank or 0 (not yet stamped from production), we do not cap — behaviour stays as today. "
			"If Batch Pcs is wrong (for example 20 when 21 bars are actually in the warehouse), we will cap to 20 and the Delivery Note will ask for 20. "
			"Please confirm Batch Pcs is correct after Finish Work Order submit.<br/><br/>"
			"<b>4. Partial reservation of a large batch</b> is unchanged except we will never stamp more Pcs than the Batch holds. "
			"We did not change how partial bars are rounded for planning.",
			s,
			border=AMBER,
		)
	)

	story.append(P("2.2 Stock Reservation Entry — who created / edited / cancelled", s["h2"]))
	story.append(
		P(
			"<b>What we changed.</b> Each Stock Reservation Entry now gets a timeline comment (Info) with:",
			s["body"],
		)
	)
	story.extend(
		bullets(
			[
				"<b>Action:</b> Created, Edited, or Cancelled.",
				"<b>User:</b> the logged-in user at that moment.",
				"<b>Date and time.</b>",
				"<b>Source:</b> Finish Work Order (with document number), Batch Wise Reservation Tool, Stock Transfer, Sales Order (Reserve Stock / submit), or Manual if none of those apply.",
			],
			s["bullet"],
		)
	)
	story.append(
		P(
			"<b>Edited</b> is logged only when reserved qty itself is changed after submit. "
			"Delivery against the reservation (delivered qty going up, status becoming Partially Delivered) is <b>not</b> treated as an edit.",
			s["body"],
		)
	)
	story.append(
		callout(
			"Possible issues from this change (please be aware)",
			"<b>1. Timeline will be busier.</b> Every new reservation adds a “Created by …” comment. This is the audit you asked for; it is not a separate report.<br/><br/>"
			"<b>2. Cancel from a Delivery Note</b> (for example Deliver-as-Qty overage) shows the <b>Delivery Note user</b> as who cancelled, and still names the original source (Finish Work Order / Sales Order). That is correct: the person who cancelled is not always the person who reserved.<br/><br/>"
			"<b>3. If a cancelled reservation is restored</b> (Delivery Note cancel-restore), the new reservation logs as <b>Created</b> again. History of the old cancelled document stays on that old document.<br/><br/>"
			"<b>4. There are no new fields on the form</b> (no extra “Created By” column). Standard Frappe owner / modified are still there; the readable log is the timeline. If you want a printed report or extra fields on the form, that is extra work — tell us.<br/><br/>"
			"<b>5. Old reservations</b> created before this change will not have a back-filled create comment. Logging starts from go-live.",
			s,
			border=AMBER,
		)
	)

	story.append(P("2.3 Finish Work Order — live Total Work Order Qty", s["h2"]))
	story.append(
		P(
			"<b>What was happening.</b> Total Work Order Qty was calculated only when the document was saved on the server. "
			"After Fetch, or while typing Ready Qty / Ready Pieces, the header total stayed blank or stale until Save.",
			s["body"],
		)
	)
	story.append(
		P(
			"<b>What we changed.</b> The header now updates immediately when:",
			s["body"],
		)
	)
	story.extend(
		bullets(
			[
				"Pending Work Orders are fetched.",
				"Actual Ready Qty or Ready Pieces is changed.",
				"A pending row is added or removed.",
			],
			s["bullet"],
		)
	)
	story.append(
		P(
			"The formula is the same as Save: <b>Total Work Order Qty = sum of Actual Ready Qty</b>, "
			"and <b>Total Work Order Pieces = sum of Ready Pieces</b>. "
			"We do <b>not</b> total Calculated Qty. Calculated Qty is used later for finished-goods posting on Sales orders where Deliver as Qty is off. "
			"Raw-material yield still uses Total Work Order Qty (Ready Qty) + Scrap, divided by 0.975. That is unchanged.",
			s["body"],
		)
	)
	story.append(
		callout(
			"Possible issues from this change (please be aware)",
			"<b>1. Display only until Save.</b> The live total is for the user on screen. Save still recalculates on the server with the same formula and then runs yield / stock checks. If someone bypasses the form, server totals win.<br/><br/>"
			"<b>2. After Fetch on a brand-new Finish Work Order,</b> the document still silent-saves so Quality Inspection warehouse is stored. That silent save still <b>does not</b> show raw-material errors. Those appear on the user’s own Save click, as today. We did not move errors onto Fetch.<br/><br/>"
			"<b>3. If Ready Qty and Calculated Qty differ</b> (normal when Deliver as Qty is off), the header Total follows Ready Qty. The finished-goods tonnes posted on submit still follow Calculated Qty. This split already existed; the live header just makes Ready Qty more visible. Do not treat the header total as the exact FG posting qty on those rows.<br/><br/>"
			"<b>4. Over-production cap on the form is still client-side.</b> Typing a Ready Qty above the allowed % is still a warning that resets the field. It is not a new server rule.",
			s,
			border=AMBER,
		)
	)

	# 4. Not done
	story.append(P("3. What we did not implement — and why we need your confirmation", s["h1"]))
	story.append(
		P(
			"These are in your issues paper, but doing them as written would change current Sales or Production. "
			"We left them off so go-live of the safe items is not blocked. Please answer Yes / No in Section 4.",
			s["body"],
		)
	)

	story.append(P("3.1 Automatic Qty on reserved Delivery Notes (Issue 1)", s["h2"]))
	story.append(
		P(
			"<b>Your request:</b> Qty = (Pcs × Length × Section Weight) / 1000, using Sales Order Pcs / Length / Section Weight, recalculate only when Pcs changes, in real time.",
			s["body"],
		)
	)
	story.append(
		P(
			"<b>What the system does today:</b> that formula already runs on Delivery Notes that are <b>not</b> reserved (no Sales Order reservation / batch bundle). "
			"On reserved Sales Delivery Notes it is deliberately switched off. Qty is the <b>reserved weight</b>. "
			"Length on the Delivery Note is the <b>actual batch length</b> (Sales Order length is shown only as reference). "
			"Pcs comes from the reservation, now capped to the Batch as in Section 2.1. "
			"We previously had to switch the formula off because it rewrote reserved qty from 0.445 to 0.443 and desynced stock from the reservation.",
			s["body"],
		)
	)
	story.append(P("<b>If we implement Issue 1 as written, these problems can appear:</b>", s["body_left"]))
	story.extend(
		bullets(
			[
				"<b>Reserved Qty will no longer match the Stock Reservation Entry.</b> Delivery, remaining reserved stock, and cancel-restore all use that weight.",
				"<b>Sales Order length is often not the mill length</b> (for example SO 6.00 m, batch 6.50 m). Using SO length in the formula changes tonnes versus physical bars.",
				"<b>Section Weight on the Sales Order / Item can differ from the batch.</b> Mixing them in one formula desyncs the Delivery Note row from the Serial and Batch Bundle.",
				"<b>Deliver-as-Qty / Invoice Qty</b> can fail the 3% tolerance, or create extra Stock Reconciliations, if physical Qty is rewritten from Pcs.",
				"<b>“Only when Pcs changes”</b> still moves Qty. On Get Items the system stamps Pcs and Qty together; that would look like a Pcs change and fire the formula on every fetch.",
			],
			s["bullet"],
		)
	)
	story.append(
		P(
			"<b>Recommendation:</b> keep the formula off on reserved / Deliver-as-Qty rows. "
			"Use it only where there is no reservation (already the case). "
			"If you still want auto Qty on reserved rows, we need a written rule: which Length (SO vs batch) and whether Invoice Qty or stock Qty should move.",
			s["body"],
		)
	)

	story.append(P("3.2 Also align Qty and Length with the Batch (Issue 1.1 remainder)", s["h2"]))
	story.append(
		P(
			"<b>Your request:</b> while fixing 21 vs 22 Pcs, also check Qty and Length.",
			s["body"],
		)
	)
	story.append(
		P(
			"<b>What we did instead:</b> Pcs is aligned to the Batch. Qty (tonnes) and Length are left as they are today — reserved weight, and batch / reservation length.",
			s["body"],
		)
	)
	story.append(P("<b>If we also reduce Qty to 21 × Length × Section Weight / 1000:</b>", s["body_left"]))
	story.extend(
		bullets(
			[
				"Sales Order remaining reserved qty changes. Finish Work Order, Batch Wise Reservation Tool and Stock Transfer allowance can disagree with what was already reserved.",
				"Delivery Note cancel-restore snapshots store that qty. Restored reservations would not match the original reserve.",
				"A few kilograms of reserved weight that the mill actually produced could be released and look “unreserved” on the Sales Order.",
			],
			s["bullet"],
		)
	)
	story.append(
		P(
			"<b>If we also force Delivery Note Length to Sales Order Length:</b> mill bars that were accepted in the ±2 m window would display and calculate as the ordered length. Dispatch papers and Stock Reconciliation length would no longer match the batch.",
			s["body"],
		)
	)
	story.append(
		P(
			"<b>Recommendation:</b> do not auto-shrink reserved Qty or overwrite Length. "
			"If a specific document is wrong, correct that reservation / batch, rather than a global formula.",
			s["body"],
		)
	)

	story.append(P("3.3 Finish Work Order — show every Save error at once (Issue 2 remainder)", s["h2"]))
	story.append(
		P(
			"<b>Your request:</b> on Save, run all validations and show every error together, not at Submit.",
			s["body"],
		)
	)
	story.append(
		P(
			"<b>What the system does today:</b> the stock / raw-material / yield / length / batch checks already run on <b>manual Save</b>, not only on Submit. "
			"Submit still repeats them as a safety net, then posts Stock Entry and reservation. "
			"The only exception is the first Fetch on a new Finish Work Order, which silent-saves without those business errors so the Quality Inspection warehouse can be stored while Raw Materials is still empty.",
			s["body"],
		)
	)
	story.append(
		P(
			"<b>What is missing vs your paper:</b> messages still stop at the <b>first</b> error (except insufficient batch stock, which is already collected). You do not yet get a single list of every problem.",
			s["body"],
		)
	)
	story.append(P("<b>If we show every error at once:</b>", s["body_left"]))
	story.extend(
		bullets(
			[
				"<b>Fetch silent-save must stay exempt.</b> If it is not, a new Finish Work Order will fail Fetch with “No Raw Materials to consume” because the RM table is cleared on Fetch.",
				"Users will see overlapping messages (for example “raw material required” and “pool exhausted” on the same empty RM table). That can look like two different faults.",
				"Error order changes. Today a missing batch is shown before yield. A full list is noisier; it does not catch new classes of error that Save does not already catch.",
			],
			s["bullet"],
		)
	)
	story.append(
		P(
			"<b>Recommendation:</b> treat “validations on Save, not Submit” as <b>already met</b>. "
			"If you still want a combined error list, confirm that Fetch auto-save must remain silent, and that duplicate/overlapping messages are acceptable.",
			s["body"],
		)
	)

	# 5. Decisions
	story.append(P("4. Decisions we need from you", s["h1"]))
	story.append(
		P(
			"Please mark Yes or No. Until we have this, the items in Section 3 stay as they are today.",
			s["body"],
		)
	)
	decisions = [
		"<b>D1.</b> Reserved Delivery Notes: keep Qty as reserved weight (recommended: <b>Yes, keep</b>), or rewrite Qty from (Pcs × Length × Section Weight) / 1000 when Pcs changes? If rewrite: use <b>Sales Order length</b> or <b>batch length</b>?",
		"<b>D2.</b> When Pcs is capped from 22 to 21, should reserved <b>Qty (tonnes)</b> also be reduced to 21 bars of weight? (Recommended: <b>No</b> — leaves a small weight vs Pcs difference, protects remaining-qty and restore.)",
		"<b>D3.</b> Delivery Note Length: keep showing <b>actual batch / mill length</b> (recommended), or force <b>Sales Order ordered length</b>?",
		"<b>D4.</b> Finish Work Order: keep showing the <b>first</b> Save error (current), or show <b>all</b> Save errors in one list? If all: confirm Fetch auto-save must stay without business errors.",
		"<b>D5.</b> Stock Reservation audit: is the <b>document timeline</b> enough, or do you also want extra fields / a report of who created, edited and cancelled?",
	]
	for d in decisions:
		story.append(P(d, s["decision"]))

	story.append(P("5. Suggested checks after go-live", s["h1"]))
	story.append(
		table(
			["Area", "What to try", "Expected result"],
			[
				[
					P("Sales — new flow", s["cell_b"]),
					P("Finish Work Order for 21 Pcs → reservation → Delivery Note via Get Items From Sales Order", s["cell"]),
					P("Batch 21, reservation 21, Delivery Note 21. No “Batch has only 21” error. Qty (tonnes) still matches reserved weight.", s["cell"]),
				],
				[
					P("Sales — old 22 Pcs reservation", s["cell_b"]),
					P("Open a Sales Order whose reservation still shows 22 Pcs. Create a new Delivery Note with Get Items From.", s["cell"]),
					P("Delivery Note Pcs comes as 21. Do not use Duplicate on an old reserved Delivery Note.", s["cell"]),
				],
				[
					P("Sales — draft already at 22", s["cell_b"]),
					P("If a draft Delivery Note already has 22 Pcs, Save once without re-fetching.", s["cell"]),
					P("Old error can still appear. Re-fetch from Sales Order, or set Pcs to 21.", s["cell"]),
				],
				[
					P("Reservation timeline", s["cell_b"]),
					P("Reserve from Sales Order; cancel a reservation; reserve from Finish Work Order.", s["cell"]),
					P("Timeline shows Created / Cancelled with user, time, and source. Delivering a Delivery Note should not add an “Edited” comment.", s["cell"]),
				],
				[
					P("Production — FWO totals", s["cell_b"]),
					P("Fetch pending work orders; change Ready Qty / Ready Pieces; add/remove a row.", s["cell"]),
					P("Header Total Work Order Qty and Pieces move immediately. Save still succeeds with the same RM yield as before.", s["cell"]),
				],
				[
					P("Production — Fetch then Save", s["cell_b"]),
					P("New Finish Work Order: Fetch (no RM yet), then add RM and Save.", s["cell"]),
					P("Fetch must not throw “No Raw Materials”. Manual Save still shows RM / yield / stock errors.", s["cell"]),
				],
				[
					P("Deliver as Qty", s["cell_b"]),
					P("One reserved Deliver-as-Qty Delivery Note and one normal reserved Delivery Note.", s["cell"]),
					P("Invoice Qty, 3% tolerance, and mill vs FG warehouse behaviour unchanged.", s["cell"]),
				],
			],
			[38 * mm, 68 * mm, 68 * mm],
		)
	)

	story.append(Spacer(1, 3 * mm))
	story.append(
		callout(
			"Bottom line",
			"The 21 vs 22 Pcs error, the reservation audit trail, and live Finish Work Order totals can go live without changing reserved tonnes, Sales Order remaining qty, or Finish Work Order yield. "
			"Automatic Qty on reserved Delivery Notes, shrinking reserved Qty/Length, and “all errors at once” on Finish Work Order Save are held until you confirm the decisions in Section 4. "
			"If any of those are approved as written, we will implement them as a separate, tested change — not mixed into this release.",
			s,
			border=GREEN,
		)
	)
	story.append(Spacer(1, 4 * mm))
	story.append(
		P(
			"This note is for review only. It does not change scope until the decisions in Section 4 are confirmed in writing.",
			s["note"],
		)
	)

	doc = SimpleDocTemplate(
		str(OUT),
		pagesize=A4,
		leftMargin=18 * mm,
		rightMargin=18 * mm,
		topMargin=20 * mm,
		bottomMargin=16 * mm,
		title="Delivery Note & FWO Implementation Note",
		author="Madhav",
	)
	doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
	print(f"Wrote {OUT}")


if __name__ == "__main__":
	build()
