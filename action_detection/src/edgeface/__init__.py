"""Vendored EdgeFace XS-GAMMA backbone (Idiap / Anjith George).

Source: https://github.com/otroshi/edgeface (backbones/timmfr.py + get_model).
License: CC BY-NC-SA 4.0 — non-commercial use only.
"""

from .model import get_edgeface_xs_gamma_06, load_edgeface_xs_gamma_06

__all__ = ["get_edgeface_xs_gamma_06", "load_edgeface_xs_gamma_06"]
