# Changelog

Format : [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) · versions : [SemVer](https://semver.org/lang/fr/).

## [0.1.0] — 2026-11-24

### Ajouté
- Serveur compatible OpenAI : `/v1/chat/completions`, `/v1/models`, `/health`, `/stats`.
- Fournisseurs : OpenAI-compatible (Ollama, vLLM, LM Studio, OpenAI…) et Anthropic (API Messages).
- Routes ordonnées avec bascule sur erreur, délai dépassé, réponse invalide ou saturation.
- Masquage réversible des données sensibles vers les fournisseurs externes
  (e-mail, téléphone malgache, CIN, carte bancaire avec contrôle de Luhn, IBAN, IPv4, motifs perso).
- Budget journalier en USD, disjoncteur, cache LRU avec TTL pour `temperature = 0`.
- Clés clients, logs sans contenu, en-têtes `X-Relais-*`.
- CLI `relais serve | check | masquer`, Dockerfile et docker-compose avec Ollama.
