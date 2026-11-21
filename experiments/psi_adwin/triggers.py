"""How a PSI alarm and an ADWIN alarm become one trigger.

``psi_gated`` fires only when ADWIN alarms and PSI alarmed in the same month
or in the previous ``gate_months - 1`` months. The default gate is 2 months.
That number is an experiment setting.
"""

from __future__ import annotations


def trigger_fires(mode: str, psi: bool, adwin: bool, recent_psi: list[bool], gate_months: int = 2) -> bool:
    if gate_months < 1:
        raise ValueError("gate_months must be at least 1")
    if mode == "psi":
        return psi
    if mode == "adwin":
        return adwin
    if mode == "psi_or_adwin":
        return psi or adwin
    if mode == "psi_and_adwin":
        return psi and adwin
    if mode == "psi_gated":
        window = recent_psi[-(gate_months - 1):] if gate_months > 1 else []
        return adwin and (psi or any(window))
    raise KeyError(mode)
