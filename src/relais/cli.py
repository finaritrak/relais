"""``relais serve`` pour lancer la passerelle, ``relais masquer`` pour tester le masquage."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from . import __version__
from .config import ErreurConfig, charger
from .redaction import Masqueur


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="relais", description="Passerelle LLM : local d'abord, cloud en secours."
    )
    parser.add_argument("--version", action="version", version=f"relais {__version__}")
    sub = parser.add_subparsers(dest="commande", required=True)

    s = sub.add_parser("serve", help="lancer le serveur")
    s.add_argument("-c", "--config", default="relais.toml")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)

    v = sub.add_parser("check", help="valider un fichier de configuration")
    v.add_argument("-c", "--config", default="relais.toml")

    m = sub.add_parser("masquer", help="montrer ce qui serait envoyé à un fournisseur externe")
    m.add_argument("texte", nargs="?", help="texte (sinon lu sur l'entrée standard)")

    args = parser.parse_args(argv)

    if args.commande == "masquer":
        texte = args.texte if args.texte is not None else sys.stdin.read()
        masque, corr = Masqueur().masquer(texte)
        print(masque)
        for jeton, valeur in corr.vers_valeur.items():
            print(f"  {jeton} = {valeur}", file=sys.stderr)
        return 0

    try:
        config = charger(args.config)
    except (OSError, ErreurConfig, ValueError) as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        return 2

    if args.commande == "check":
        print(f"✓ {len(config.fournisseurs)} fournisseur(s), routes : {', '.join(config.routes)}")
        for route, chaine in config.routes.items():
            etapes = " → ".join(
                f"{n}{' (externe, masqué)' if config.fournisseurs[n].externe else ' (local)'}" for n in chaine
            )
            print(f"  {route} : {etapes}")
        if not config.cles_clients:
            print("  ⚠ aucune clé client : la passerelle est ouverte à quiconque y accède")
        return 0

    import uvicorn  # import tardif : la CLI « masquer » n'en a pas besoin

    from .app import creer_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    uvicorn.run(creer_app(config), host=args.host, port=args.port)  # pragma: no cover
    return 0  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
