"""Masquage réversible des données sensibles.

Avant d'envoyer une requête à un fournisseur **externe** (cloud), chaque donnée
sensible est remplacée par un jeton (``⟦EMAIL_1⟧``). La réponse du modèle est
ensuite « démasquée » : l'utilisateur voit les vraies valeurs, le fournisseur jamais.

Détecté par défaut : e-mails, IBAN, cartes bancaires (contrôle de Luhn),
CIN malgaches (12 chiffres), téléphones malgaches, adresses IPv4.
Les noms de personnes ne sont **pas** détectés (il faudrait un modèle NER) :
ajoutez vos propres motifs dans la configuration si besoin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Pattern, Tuple

Validator = Callable[[str], bool]


def luhn_valide(numero: str) -> bool:
    chiffres = [int(c) for c in numero if c.isdigit()]
    if not 13 <= len(chiffres) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(chiffres)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def ipv4_valide(texte: str) -> bool:
    return all(0 <= int(p) <= 255 for p in texte.split("."))


@dataclass(frozen=True)
class Motif:
    nom: str
    regex: Pattern[str]
    valider: Optional[Validator] = None


# L'ordre compte : les motifs les plus spécifiques passent en premier.
MOTIFS_PAR_DEFAUT: Tuple[Motif, ...] = (
    Motif("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    Motif("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")),
    Motif("CARTE", re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"), luhn_valide),
    Motif("CIN", re.compile(r"(?<!\d)\d{3}[ .]?\d{3}[ .]?\d{3}[ .]?\d{3}(?!\d)")),
    Motif("TEL", re.compile(r"(?<![\d+])(?:\+?261[ .-]?|0)3[2-9](?:[ .-]?\d){7}(?!\d)")),
    Motif("IP", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"), ipv4_valide),
)

_JETON = re.compile(r"⟦([A-Z_]+)_(\d+)⟧")


@dataclass
class Correspondances:
    """Table jeton → valeur pour une requête (jamais persistée)."""

    vers_valeur: Dict[str, str] = field(default_factory=dict)
    vers_jeton: Dict[str, str] = field(default_factory=dict)
    compteurs: Dict[str, int] = field(default_factory=dict)

    def jeton_pour(self, categorie: str, valeur: str) -> str:
        if valeur in self.vers_jeton:
            return self.vers_jeton[valeur]
        n = self.compteurs.get(categorie, 0) + 1
        self.compteurs[categorie] = n
        jeton = f"⟦{categorie}_{n}⟧"
        self.vers_jeton[valeur] = jeton
        self.vers_valeur[jeton] = valeur
        return jeton

    def __len__(self) -> int:
        return len(self.vers_valeur)


class Masqueur:
    def __init__(
        self,
        categories: Optional[Iterable[str]] = None,
        motifs_perso: Optional[Dict[str, str]] = None,
    ) -> None:
        actifs = {c.upper() for c in categories} if categories is not None else None
        motifs: List[Motif] = [
            Motif(f"PERSO_{nom.upper()}", re.compile(regex)) for nom, regex in (motifs_perso or {}).items()
        ]
        motifs += [m for m in MOTIFS_PAR_DEFAUT if actifs is None or m.nom in actifs]
        self.motifs = tuple(motifs)

    def masquer(self, texte: str, corr: Optional[Correspondances] = None) -> Tuple[str, Correspondances]:
        corr = corr if corr is not None else Correspondances()
        for motif in self.motifs:

            def remplacer(m: re.Match[str], motif: Motif = motif) -> str:
                valeur = m.group(0)
                if motif.valider and not motif.valider(valeur):
                    return valeur
                return corr.jeton_pour(motif.nom, valeur)

            texte = motif.regex.sub(remplacer, texte)
        return texte, corr

    @staticmethod
    def demasquer(texte: str, corr: Correspondances) -> str:
        if not corr:
            return texte
        return _JETON.sub(lambda m: corr.vers_valeur.get(m.group(0), m.group(0)), texte)
