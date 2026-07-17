"""Best-effort sensors that do not require MSI Afterburner."""

from __future__ import annotations

import subprocess
from typing import Optional

_CREATE_NO_WINDOW = 0x08000000


def read_wmi_thermal_c() -> Optional[float]:
    """ACPI thermal zone °C via PowerShell. Often chassis, not CPU die."""
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -EA SilentlyContinue |"
                " Sort-Object CurrentTemperature -Descending | Select-Object -First 1)."
                "CurrentTemperature",
            ],
            text=True,
            timeout=2.0,
            creationflags=_CREATE_NO_WINDOW,
        )
        raw = out.strip().splitlines()
        if not raw:
            return None
        tenths_k = float(raw[0].replace(",", "."))
        # WMI stores (Kelvin * 10)
        c = (tenths_k - 2732.0) / 10.0
        if c < 0 or c > 125:
            return None
        return c
    except Exception:
        return None
