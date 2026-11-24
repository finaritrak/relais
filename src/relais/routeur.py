"""Routage : local d'abord, bascule en cas d'erreur, de saturation ou de panne,
avec masquage pour les fournisseurs externes, plafond de budget et cache."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from .config import Config
from .fournisseurs import ErreurFournisseur, Fournisseur, Reponse, creer, reponse_openai
from .redaction import Correspondances, Masqueur

journal = logging.getLogger("relais")


class AucunFournisseur(Exception):
    """Tous les fournisseurs de la route ont échoué ou ont été écartés."""

    def __init__(self, route: str, essais: List[Dict[str, str]]) -> None:
        super().__init__(f"Aucun fournisseur disponible pour la route {route!r}")
        self.route = route
        self.essais = essais


class RouteInconnue(KeyError):
    pass


@dataclass
class Resultat:
    corps: Dict[str, Any]
    fournisseur: str
    masques: int
    depuis_cache: bool
    essais: List[Dict[str, str]]
    cout_usd: float = 0.0


@dataclass
class EtatFournisseur:
    semaphore: asyncio.Semaphore
    echecs_consecutifs: int = 0
    ouvert_jusqu_a: float = 0.0
    requetes: int = 0
    erreurs: int = 0
    jetons_entree: int = 0
    jetons_sortie: int = 0
    cout_usd: float = 0.0


@dataclass
class Budget:
    plafond_usd: Optional[float]
    jour: date = field(default_factory=date.today)
    depense_usd: float = 0.0
    aujourd_hui: Callable[[], date] = date.today

    def _rafraichir(self) -> None:
        j = self.aujourd_hui()
        if j != self.jour:
            self.jour, self.depense_usd = j, 0.0

    def epuise(self) -> bool:
        self._rafraichir()
        return self.plafond_usd is not None and self.depense_usd >= self.plafond_usd

    def ajouter(self, montant: float) -> None:
        self._rafraichir()
        self.depense_usd += montant


class Cache:
    def __init__(self, taille: int, ttl: float, horloge: Callable[[], float] = time.monotonic) -> None:
        self.taille, self.ttl, self.horloge = taille, ttl, horloge
        self._donnees: OrderedDict[str, Tuple[float, Dict[str, Any], str]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def cle(route: str, messages: List[Dict[str, Any]], params: Dict[str, Any]) -> str:
        brut = json.dumps([route, messages, params], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(brut.encode()).hexdigest()

    def lire(self, cle: str) -> Optional[Tuple[Dict[str, Any], str]]:
        entree = self._donnees.get(cle)
        if entree is None or self.horloge() - entree[0] > self.ttl:
            if entree is not None:
                del self._donnees[cle]
            self.misses += 1
            return None
        self._donnees.move_to_end(cle)
        self.hits += 1
        return copy.deepcopy(entree[1]), entree[2]

    def ecrire(self, cle: str, corps: Dict[str, Any], fournisseur: str) -> None:
        self._donnees[cle] = (self.horloge(), copy.deepcopy(corps), fournisseur)
        self._donnees.move_to_end(cle)
        while len(self._donnees) > self.taille:
            self._donnees.popitem(last=False)


class Routeur:
    def __init__(
        self,
        config: Config,
        client: Optional[httpx.AsyncClient] = None,
        *,
        horloge: Callable[[], float] = time.monotonic,
        aujourd_hui: Callable[[], date] = date.today,
    ) -> None:
        self.config = config
        self.client = client or httpx.AsyncClient()
        self.horloge = horloge
        self.fournisseurs: Dict[str, Fournisseur] = {
            nom: creer(cfg, self.client) for nom, cfg in config.fournisseurs.items()
        }
        self.etats: Dict[str, EtatFournisseur] = {
            nom: EtatFournisseur(asyncio.Semaphore(cfg.max_concurrence))
            for nom, cfg in config.fournisseurs.items()
        }
        self.masqueur = Masqueur(config.masquage_categories, config.masquage_motifs_perso)
        self.budget = Budget(config.budget_journalier_usd, jour=aujourd_hui(), aujourd_hui=aujourd_hui)
        self.cache = (
            Cache(config.cache_taille, config.cache_ttl_secondes, horloge) if config.cache_actif else None
        )

    # ------------------------------------------------------------------ API

    async def completer(self, route: str, messages: List[Dict[str, Any]], params: Dict[str, Any]) -> Resultat:
        chaine = self.config.routes.get(route)
        if chaine is None:
            raise RouteInconnue(route)

        cle = None
        if self.cache is not None and params.get("temperature") == 0:
            cle = Cache.cle(route, messages, params)
            trouve = self.cache.lire(cle)
            if trouve:
                corps, fournisseur = trouve
                return Resultat(corps, fournisseur, 0, True, [])

        essais: List[Dict[str, str]] = []
        for i, nom in enumerate(chaine):
            dernier = i == len(chaine) - 1
            raison = self._ecarter(nom, dernier)
            if raison:
                essais.append({"fournisseur": nom, "resultat": raison})
                continue
            try:
                resultat = await self._essayer(nom, route, messages, params)
            except ErreurFournisseur as exc:
                essais.append({"fournisseur": nom, "resultat": f"erreur : {exc}"})
                journal.warning("relais: %s a échoué (%s), bascule", nom, exc)
                if not exc.reessayable:
                    break
                continue
            resultat.essais = essais + [{"fournisseur": nom, "resultat": "ok"}]
            if cle is not None and self.cache is not None:
                self.cache.ecrire(cle, resultat.corps, nom)
            return resultat
        raise AucunFournisseur(route, essais)

    def stats(self) -> Dict[str, Any]:
        return {
            "budget": {
                "jour": self.budget.jour.isoformat(),
                "depense_usd": round(self.budget.depense_usd, 6),
                "plafond_usd": self.budget.plafond_usd,
            },
            "cache": (
                {"hits": self.cache.hits, "misses": self.cache.misses, "entrees": len(self.cache._donnees)}
                if self.cache
                else None
            ),
            "fournisseurs": {
                nom: {
                    "externe": self.config.fournisseurs[nom].externe,
                    "requetes": e.requetes,
                    "erreurs": e.erreurs,
                    "jetons_entree": e.jetons_entree,
                    "jetons_sortie": e.jetons_sortie,
                    "cout_usd": round(e.cout_usd, 6),
                    "disjoncteur_ouvert": e.ouvert_jusqu_a > self.horloge(),
                }
                for nom, e in self.etats.items()
            },
        }

    async def fermer(self) -> None:
        await self.client.aclose()

    # ------------------------------------------------------------- interne

    def _ecarter(self, nom: str, dernier: bool) -> Optional[str]:
        etat, cfg = self.etats[nom], self.config.fournisseurs[nom]
        if etat.ouvert_jusqu_a > self.horloge():
            return "écarté : disjoncteur ouvert (pannes récentes)"
        payant = cfg.prix_entree_par_million > 0 or cfg.prix_sortie_par_million > 0
        if payant and self.budget.epuise():
            return "écarté : budget journalier atteint"
        if etat.semaphore.locked() and not dernier:
            return "écarté : saturé (concurrence maximale atteinte)"
        return None

    async def _essayer(
        self, nom: str, route: str, messages: List[Dict[str, Any]], params: Dict[str, Any]
    ) -> Resultat:
        fournisseur, etat, cfg = self.fournisseurs[nom], self.etats[nom], self.config.fournisseurs[nom]
        corr = Correspondances()
        a_envoyer = messages
        if cfg.externe and self.config.masquage_actif:
            a_envoyer = [self._masquer_message(m, corr) for m in messages]

        async with etat.semaphore:
            etat.requetes += 1
            try:
                rep: Reponse = await fournisseur.completer(a_envoyer, params)
            except ErreurFournisseur:
                etat.erreurs += 1
                etat.echecs_consecutifs += 1
                if etat.echecs_consecutifs >= self.config.disjoncteur_echecs:
                    etat.ouvert_jusqu_a = self.horloge() + self.config.disjoncteur_pause_secondes
                    etat.echecs_consecutifs = 0
                raise
        etat.echecs_consecutifs = 0
        cout = fournisseur.cout(rep)
        etat.jetons_entree += rep.jetons_entree
        etat.jetons_sortie += rep.jetons_sortie
        etat.cout_usd += cout
        self.budget.ajouter(cout)

        texte = Masqueur.demasquer(rep.texte, corr)
        journal.info(
            "relais route=%s fournisseur=%s entree=%d sortie=%d cout=%.6f masques=%d",
            route, nom, rep.jetons_entree, rep.jetons_sortie, cout, len(corr),
        )  # fmt: skip
        return Resultat(reponse_openai(rep, route, texte), nom, len(corr), False, [], cout)

    def _masquer_message(self, message: Dict[str, Any], corr: Correspondances) -> Dict[str, Any]:
        m = dict(message)
        contenu = m.get("content")
        if isinstance(contenu, str):
            m["content"], _ = self.masqueur.masquer(contenu, corr)
        elif isinstance(contenu, list):
            parties = []
            for p in contenu:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    p = {**p, "text": self.masqueur.masquer(p["text"], corr)[0]}
                parties.append(p)
            m["content"] = parties
        return m
