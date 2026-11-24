"""Configuration TOML de relais (voir ``relais.example.toml``)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


class ErreurConfig(ValueError):
    pass


@dataclass(frozen=True)
class ConfigFournisseur:
    nom: str
    type: str  # "openai" (OpenAI, Ollama, vLLM, LM Studio…) ou "anthropic"
    base_url: str
    modele: str
    externe: bool  # True = les données quittent votre réseau → masquage
    cle_api: Optional[str] = None
    timeout: float = 60.0
    max_concurrence: int = 8
    prix_entree_par_million: float = 0.0  # USD par million de jetons d'entrée
    prix_sortie_par_million: float = 0.0
    max_tokens_defaut: int = 1024


@dataclass(frozen=True)
class Config:
    fournisseurs: Dict[str, ConfigFournisseur]
    routes: Dict[str, List[str]]
    cles_clients: List[str] = field(default_factory=list)
    budget_journalier_usd: Optional[float] = None
    masquage_actif: bool = True
    masquage_categories: Optional[List[str]] = None
    masquage_motifs_perso: Dict[str, str] = field(default_factory=dict)
    cache_actif: bool = True
    cache_taille: int = 1000
    cache_ttl_secondes: int = 3600
    disjoncteur_echecs: int = 3
    disjoncteur_pause_secondes: float = 30.0


def _cle(valeur: Any, env_nom: Optional[str]) -> Optional[str]:
    if env_nom:
        v = os.environ.get(env_nom)
        if not v:
            raise ErreurConfig(f"Variable d'environnement {env_nom} absente")
        return v
    return None if valeur is None else str(valeur)


def charger(chemin: str | Path) -> Config:
    with open(chemin, "rb") as f:
        return depuis_dict(tomllib.load(f))


def depuis_dict(data: Dict[str, Any]) -> Config:
    fournisseurs: Dict[str, ConfigFournisseur] = {}
    for p in data.get("fournisseurs", []):
        nom = p.get("nom")
        if not nom:
            raise ErreurConfig("Chaque fournisseur doit avoir un 'nom'")
        type_ = p.get("type", "openai")
        if type_ not in ("openai", "anthropic"):
            raise ErreurConfig(f"{nom}: type inconnu {type_!r} (openai ou anthropic)")
        for requis in ("base_url", "modele"):
            if not p.get(requis):
                raise ErreurConfig(f"{nom}: '{requis}' manquant")
        fournisseurs[nom] = ConfigFournisseur(
            nom=nom,
            type=type_,
            base_url=p["base_url"].rstrip("/"),
            modele=p["modele"],
            externe=bool(p.get("externe", type_ == "anthropic")),
            cle_api=_cle(p.get("cle_api"), p.get("cle_api_env")),
            timeout=float(p.get("timeout", 60)),
            max_concurrence=int(p.get("max_concurrence", 8)),
            prix_entree_par_million=float(p.get("prix_entree_par_million", 0)),
            prix_sortie_par_million=float(p.get("prix_sortie_par_million", 0)),
            max_tokens_defaut=int(p.get("max_tokens_defaut", 1024)),
        )
    if not fournisseurs:
        raise ErreurConfig("Aucun fournisseur configuré")

    routes = {nom: list(chaine) for nom, chaine in data.get("routes", {}).items()}
    if not routes:
        routes = {"defaut": list(fournisseurs)}
    for nom, chaine in routes.items():
        if not chaine:
            raise ErreurConfig(f"Route {nom!r} vide")
        for f in chaine:
            if f not in fournisseurs:
                raise ErreurConfig(f"Route {nom!r} : fournisseur inconnu {f!r}")

    serveur = data.get("serveur", {})
    cles = list(serveur.get("cles_clients", []))
    if serveur.get("cles_clients_env"):
        cles += [c for c in os.environ.get(serveur["cles_clients_env"], "").split(",") if c]
    budget = data.get("budget", {})
    masquage = data.get("masquage", {})
    cache = data.get("cache", {})
    disj = data.get("disjoncteur", {})
    return Config(
        fournisseurs=fournisseurs,
        routes=routes,
        cles_clients=cles,
        budget_journalier_usd=budget.get("journalier_usd"),
        masquage_actif=bool(masquage.get("actif", True)),
        masquage_categories=masquage.get("categories"),
        masquage_motifs_perso=dict(masquage.get("motifs_perso", {})),
        cache_actif=bool(cache.get("actif", True)),
        cache_taille=int(cache.get("taille", 1000)),
        cache_ttl_secondes=int(cache.get("ttl_secondes", 3600)),
        disjoncteur_echecs=int(disj.get("echecs", 3)),
        disjoncteur_pause_secondes=float(disj.get("pause_secondes", 30)),
    )
