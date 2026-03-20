from __future__ import annotations

from models.task_spec import LanguageCode
from utils.text import contains_any


LANG_KEYWORDS: dict[LanguageCode, set[str]] = {
    "nb": {"opprett", "faktura", "kunde", "ansatt", "lonn", "betaling", "prosjekt"},
    "nn": {"opprett", "faktura", "kunde", "reiserekning", "prosjektleiar", "timar"},
    "en": {"create", "invoice", "customer", "employee", "payment", "project", "supplier", "register"},
    "es": {"crea", "factura", "cliente", "pedido", "proyecto", "pago", "asiento", "dimension", "registre"},
    "pt": {"crie", "fatura", "cliente", "pedido", "projeto", "pagamento", "despesa", "viagem", "registe", "ajudas de custo"},
    "de": {"erstellen", "rechnung", "kunde", "projekt", "zahlung", "stunden"},
    "fr": {"creez", "facture", "client", "projet", "paiement", "salaire", "comptabilisez", "piece", "compte"},
}


def detect_language(prompt_normalized: str) -> LanguageCode:
    hits: dict[LanguageCode, int] = {code: 0 for code in LANG_KEYWORDS}
    for code, keywords in LANG_KEYWORDS.items():
        for key in keywords:
            if contains_any(prompt_normalized, {key}):
                hits[code] += 1

    best = max(hits.items(), key=lambda item: item[1])
    return best[0] if best[1] > 0 else "unknown"
