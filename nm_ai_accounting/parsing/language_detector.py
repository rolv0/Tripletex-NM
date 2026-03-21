from __future__ import annotations

from models.task_spec import LanguageCode
from utils.text import contains_any


LANG_KEYWORDS: dict[LanguageCode, dict[str, set[str]]] = {
    "nb": {
        "strong": {"opprett", "faktura", "kunde", "ansatt", "lonn", "betaling", "prosjekt", "leverandor", "avdeling"},
        "weak": {"med", "for", "og", "epost", "orgnr", "reiseregning"},
    },
    "nn": {
        "strong": {"opprett", "faktura", "kunde", "reiserekning", "prosjektleiar", "timar", "reiserekning"},
        "weak": {"med", "for", "og", "dagar", "avdelingar"},
    },
    "en": {
        "strong": {"create", "invoice", "customer", "employee", "payment", "project", "supplier", "register", "travel expense", "hours", "activity"},
        "weak": {"with", "for", "email", "organization number", "base salary", "hourly rate"},
    },
    "es": {
        "strong": {"crea", "factura", "cliente", "pedido", "proyecto", "pago", "asiento", "dimension", "proveedor", "cuenta"},
        "weak": {"con", "para", "luego", "numero de organizacion", "gasto de viaje"},
    },
    "pt": {
        "strong": {"crie", "fatura", "cliente", "pedido", "projeto", "pagamento", "despesa", "viagem", "registe", "ajudas de custo", "fornecedor"},
        "weak": {"com", "para", "referente", "conta", "despesa de viagem"},
    },
    "de": {
        "strong": {"erstellen", "rechnung", "kunde", "projekt", "zahlung", "stunden", "lieferant", "konto", "aktivitat"},
        "weak": {"mit", "fur", "und", "mwst", "reise"},
    },
    "fr": {
        "strong": {"creez", "facture", "client", "projet", "paiement", "salaire", "comptabilisez", "piece", "compte", "fournisseur"},
        "weak": {"avec", "pour", "puis", "numero d organisation", "note de credit", "depense de voyage"},
    },
}


def detect_language(prompt_normalized: str) -> LanguageCode:
    hits: dict[LanguageCode, float] = {code: 0.0 for code in LANG_KEYWORDS}
    for code, buckets in LANG_KEYWORDS.items():
        for key in buckets["strong"]:
            if contains_any(prompt_normalized, {key}):
                hits[code] += 2.0
        for key in buckets["weak"]:
            if contains_any(prompt_normalized, {key}):
                hits[code] += 0.7

    best = max(hits.items(), key=lambda item: item[1])
    return best[0] if best[1] > 0 else "unknown"
