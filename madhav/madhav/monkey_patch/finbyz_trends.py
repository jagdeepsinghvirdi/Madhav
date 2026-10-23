# Copyright (c) 2026, Finbyz pvt. ltd. and contributors
# For license information, please see license.txt

import sys


def _as_select_string(value):
	if not isinstance(value, (list, tuple)):
		return value

	parts = []
	for part in value:
		part = str(part).strip()
		if not part:
			continue
		if part.endswith(","):
			part = part[:-1].strip()
		if part:
			parts.append(part)

	return ", ".join(parts)


def _wrap_get_data(original):
	def get_data(filters, conditions):
		if isinstance(conditions, dict):
			for key in ("based_on_select", "period_wise_select"):
				if key in conditions:
					conditions[key] = _as_select_string(conditions[key])
		return original(filters, conditions)

	return get_data


def apply():
	try:
		from finbyzerp.finbyzerp.override.report import trends
	except Exception:
		return

	if getattr(trends, "_madhav_trends_select_patched", False):
		return

	original = getattr(trends, "get_data", None)
	if original is None:
		return

	patched = _wrap_get_data(original)
	trends.get_data = patched
	trends._madhav_trends_select_patched = True

	for name, module in list(sys.modules.items()):
		if not name.startswith("finbyzerp.") or module is trends or module is None:
			continue
		if getattr(module, "get_data", None) is original:
			module.get_data = patched


apply()
