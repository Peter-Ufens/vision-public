"""Couche legere Windows / X11 / Wayland.

Le nom n'est pas ``platform`` : ce module existe deja dans Python.
Aucune souris ici : ce depot ne fait que lire l'ecran.
"""

from plateforme.session import BackendManquant, nom

__all__ = ["BackendManquant", "nom"]
