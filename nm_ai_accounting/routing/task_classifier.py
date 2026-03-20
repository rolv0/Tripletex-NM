from __future__ import annotations

from dataclasses import dataclass

from models import Entity, TaskSpec
from parsing.attachment_parser import ParsedAttachment
from parsing.entity_extractor import extract_all_entities
from parsing.language_detector import detect_language
from parsing.prompt_normalizer import normalize_prompt
from utils.text import contains_any

CREATE_WORDS = {
    "create",
    "opprett",
    "lag",
    "registrer",
    "register",
    "crea",
    "crie",
    "erstellen",
    "creez",
    "registe",
}
UPDATE_WORDS = {"update", "oppdater", "endre", "actualizar", "aktualisieren", "modifier"}
DELETE_WORDS = {"delete", "remove", "slett", "fjern", "eliminar", "supprimer", "loschen"}
PAYMENT_WORDS = {"payment", "betaling", "pago", "pagamento", "zahlung", "paiement", "delbetaling"}
INVOICE_WORDS = {"invoice", "faktura", "factura", "fatura", "rechnung", "facture"}
ORDER_WORDS = {"order", "ordre", "pedido", "encomenda", "bestellung", "commande"}
CONVERT_WORDS = {"convert", "konverter", "convierte", "converter", "umwandeln", "convertir"}
PROJECT_WORDS = {"project", "prosjekt", "proyecto", "projet", "projekt", "project manager", "prosjektleiar"}
PRODUCT_WORDS = {"product", "produkt", "producto", "produit", "product number", "produktnummer"}
CUSTOMER_WORDS = {"customer", "kunde", "cliente", "client", "kunden"}
SUPPLIER_WORDS = {"supplier", "leverandor", "fornecedor", "fournisseur", "proveedor", "lieferant", "vendor"}
EMPLOYEE_WORDS = {"employee", "ansatt", "empleado", "mitarbeiter", "salarie", "new employee"}
DEPARTMENT_WORDS = {"department", "avdeling", "departamento", "departement", "abteilung"}
PAYROLL_WORDS = {"salary", "payroll", "lonn", "loenn", "salaire", "salario", "paie", "bonus", "base salary"}
TRAVEL_WORDS = {
    "travel",
    "reise",
    "expense",
    "reiseregning",
    "reiserekning",
    "deplacement",
    "viagem",
    "despesa",
    "despesa de viagem",
    "voyage",
    "ajudas de custo",
    "diett",
    "diet allowance",
}
CREDIT_WORDS = {"credit note", "kreditnota", "gutschrift", "avoir", "nota de credito", "nota de credito completa"}
LEDGER_WORDS = {
    "ledger",
    "hovedbok",
    "asiento",
    "asiento contable",
    "buchung",
    "ecriture",
    "journal entry",
    "posting",
    "bokfor",
    "bokforing",
    "comptabilisez",
    "comptabiliser",
    "piece",
    "pieza",
    "voucher",
    "bilag",
    "journal",
    "compte",
    "cuenta",
    "conta",
    "account",
    "konto",
}
DIMENSION_WORDS = {
    "dimension",
    "dimensjon",
    "dimensione",
    "dimension contable",
    "dimension comptable",
    "accounting dimension",
    "dimension personnalis",
    "personalisada",
    "custom dimension",
}


@dataclass(frozen=True)
class FamilyRule:
    primary: set[str]
    secondary: set[str]
    negative: set[str]
    base_confidence: float
    actions: list[str]


FAMILY_RULES: dict[str, FamilyRule] = {
    "register_payment": FamilyRule(
        primary=PAYMENT_WORDS | {"outstanding invoice", "utestaende faktura", "full payment", "register payment"},
        secondary=INVOICE_WORDS,
        negative=PAYROLL_WORDS | TRAVEL_WORDS,
        base_confidence=0.9,
        actions=["find_invoice", "register_payment"],
    ),
    "order_to_invoice": FamilyRule(
        primary=ORDER_WORDS | INVOICE_WORDS | CONVERT_WORDS,
        secondary=CUSTOMER_WORDS | CREATE_WORDS,
        negative=PAYROLL_WORDS | TRAVEL_WORDS,
        base_confidence=0.95,
        actions=["ensure_customer", "create_order", "create_invoice"],
    ),
    "create_credit_note": FamilyRule(
        primary=CREDIT_WORDS | {"reverse invoice", "reclamerte", "revert the invoice"},
        secondary=INVOICE_WORDS,
        negative=PAYMENT_WORDS | PAYROLL_WORDS,
        base_confidence=0.9,
        actions=["find_invoice", "create_credit_note"],
    ),
    "salary_transaction": FamilyRule(
        primary=PAYROLL_WORDS | {"salary transaction", "run payroll", "execute payroll"},
        secondary={"employee", "bonus", "base salary", "grunnlonn"},
        negative=INVOICE_WORDS | ORDER_WORDS | TRAVEL_WORDS,
        base_confidence=0.88,
        actions=["find_employee", "create_salary_transaction"],
    ),
    "delete_travel_expense": FamilyRule(
        primary=TRAVEL_WORDS | DELETE_WORDS,
        secondary={"travel expense", "reiseregning"},
        negative=PAYROLL_WORDS | INVOICE_WORDS,
        base_confidence=0.86,
        actions=["find_travel_expense", "delete_travel_expense"],
    ),
    "create_travel_expense": FamilyRule(
        primary=TRAVEL_WORDS | {"conference", "konferanse", "conferencia", "kundebesok", "reiseutlegg"},
        secondary={"day", "days", "dag", "dias", "jours", "taxi", "flight", "flybillett"},
        negative=DELETE_WORDS | PAYROLL_WORDS,
        base_confidence=0.85,
        actions=["find_employee", "create_travel_expense"],
    ),
    "create_invoice": FamilyRule(
        primary=INVOICE_WORDS | CREATE_WORDS,
        secondary=CUSTOMER_WORDS | {"send invoice", "send", "hors tva", "ekskl mva"},
        negative=PAYMENT_WORDS | CREDIT_WORDS | ORDER_WORDS,
        base_confidence=0.86,
        actions=["ensure_customer", "create_invoice"],
    ),
    "create_project": FamilyRule(
        primary=PROJECT_WORDS,
        secondary=CUSTOMER_WORDS | {"fixed price", "fastpris", "project manager", "prosjektleiar"},
        negative=PAYROLL_WORDS | TRAVEL_WORDS,
        base_confidence=0.84,
        actions=["ensure_customer", "create_or_update_project"],
    ),
    "create_product": FamilyRule(
        primary=PRODUCT_WORDS,
        secondary={"price", "preis", "25 %", "mva", "iva", "vat", "mwst"},
        negative=INVOICE_WORDS | ORDER_WORDS,
        base_confidence=0.9,
        actions=["create_product"],
    ),
    "create_employee": FamilyRule(
        primary=EMPLOYEE_WORDS,
        secondary={"email", "start date", "administrator", "born", "fodselsdato", "birth"},
        negative=PAYROLL_WORDS | TRAVEL_WORDS,
        base_confidence=0.9,
        actions=["create_employee"],
    ),
    "create_department": FamilyRule(
        primary=DEPARTMENT_WORDS,
        secondary={"tre", "three", "avdelinger", "departments", "dimensions"},
        negative=LEDGER_WORDS | TRAVEL_WORDS,
        base_confidence=0.84,
        actions=["create_departments"],
    ),
    "create_supplier": FamilyRule(
        primary=SUPPLIER_WORDS,
        secondary={"invoice email", "faktura", "organization number", "org", "email"},
        negative=CUSTOMER_WORDS | PAYROLL_WORDS,
        base_confidence=0.86,
        actions=["create_supplier"],
    ),
    "ledger_correction": FamilyRule(
        primary=DIMENSION_WORDS | LEDGER_WORDS,
        secondary={"7100", "7300", "10150", "43400", "custom accounting dimension", "linked to"},
        negative=PAYROLL_WORDS | TRAVEL_WORDS,
        base_confidence=0.84,
        actions=["create_accounting_dimension", "create_dimension_values", "register_voucher"],
    ),
    "create_customer": FamilyRule(
        primary=CUSTOMER_WORDS,
        secondary={"organization number", "org", "adresse", "address", "email", "post@"},
        negative=SUPPLIER_WORDS | PAYROLL_WORDS,
        base_confidence=0.8,
        actions=["create_customer"],
    ),
}


def _intent(prompt_n: str) -> str:
    if contains_any(prompt_n, CREATE_WORDS):
        return "create"
    if contains_any(prompt_n, UPDATE_WORDS):
        return "update"
    if contains_any(prompt_n, DELETE_WORDS):
        return "delete"
    if contains_any(prompt_n, PAYMENT_WORDS):
        return "register"
    return "unknown"


def _score_keywords(prompt_n: str, keywords: set[str], weight: float, limit: int | None = None) -> float:
    hits = 0
    for keyword in keywords:
        if contains_any(prompt_n, {keyword}):
            hits += 1
    if limit is not None:
        hits = min(hits, limit)
    return hits * weight


def _score_family(prompt_n: str, family: str, entities_map: dict[str, object]) -> float:
    rule = FAMILY_RULES[family]
    score = 0.0
    score += _score_keywords(prompt_n, rule.primary, 2.2, limit=3)
    score += _score_keywords(prompt_n, rule.secondary, 0.9, limit=4)
    score -= _score_keywords(prompt_n, rule.negative, 1.3, limit=2)

    has_email = bool(entities_map.get("email"))
    has_org = bool(entities_map.get("organizationNumber"))
    has_customer_name = bool(entities_map.get("customerName"))
    amounts = entities_map.get("amounts") or []
    quoted_items = entities_map.get("quotedItems") or []

    if family in {"create_customer", "create_supplier"}:
        if has_org:
            score += 1.1
        if has_email:
            score += 0.5
        if has_customer_name:
            score += 0.8
    if family == "create_travel_expense":
        if has_email:
            score += 0.7
        if amounts:
            score += 0.5
    if family == "ledger_correction":
        if amounts:
            score += 0.5
        if quoted_items:
            score += 0.4
    if family == "create_invoice":
        if has_customer_name or has_org:
            score += 0.7
        if amounts:
            score += 0.6
    if family == "order_to_invoice":
        if len(amounts) >= 2:
            score += 0.7
        if quoted_items:
            score += 0.5
    if family == "salary_transaction" and has_email:
        score += 0.6

    return score


def _pick_task_family(prompt_n: str, entities_map: dict[str, object]) -> tuple[str, list[str], float, list[str]]:
    has_invoice = contains_any(prompt_n, INVOICE_WORDS)
    has_order = contains_any(prompt_n, ORDER_WORDS)
    has_payment = contains_any(prompt_n, PAYMENT_WORDS)
    has_convert = contains_any(prompt_n, CONVERT_WORDS)
    has_create = contains_any(prompt_n, CREATE_WORDS)

    if has_order and has_invoice and has_convert:
        return "order_to_invoice", FAMILY_RULES["order_to_invoice"].actions, 0.95, []
    if has_payment and has_invoice and not (has_order and has_create):
        return "register_payment", FAMILY_RULES["register_payment"].actions, 0.9, []
    if contains_any(prompt_n, CREDIT_WORDS) and has_invoice:
        return "create_credit_note", FAMILY_RULES["create_credit_note"].actions, 0.9, []
    if contains_any(prompt_n, DIMENSION_WORDS) and (
        contains_any(prompt_n, LEDGER_WORDS) or any(number in prompt_n for number in ("7100", "7300"))
    ):
        return "ledger_correction", FAMILY_RULES["ledger_correction"].actions, 0.88, []

    scores = {family: _score_family(prompt_n, family, entities_map) for family in FAMILY_RULES}
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_family, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else -999.0
    margin = best_score - second_score

    if best_score < 2.2:
        return "unknown", [], 0.55, ["unclassified_prompt", "low_evidence"]

    risk_flags: list[str] = []
    confidence = min(0.97, FAMILY_RULES[best_family].base_confidence + min(best_score / 20.0, 0.08))
    if margin < 0.8:
        risk_flags.append("low_margin_classification")
        confidence = max(0.6, confidence - 0.12)

    return best_family, FAMILY_RULES[best_family].actions, confidence, risk_flags


def classify_task(prompt: str, attachments: list[ParsedAttachment]) -> TaskSpec:
    prompt_n = normalize_prompt(prompt)
    attachment_texts = [a.extracted_text for a in attachments if a.extracted_text]
    language = detect_language(prompt_n)
    entities_map = extract_all_entities(prompt, attachment_texts)
    entities = [
        Entity(type="customer", data={"name": entities_map.get("customerName"), "organizationNumber": entities_map.get("organizationNumber")}),
        Entity(type="person", data={"name": entities_map.get("personName"), "email": entities_map.get("email")}),
        Entity(type="project", data={"name": entities_map.get("projectName")}),
    ]

    task_family, actions, confidence, risk_flags = _pick_task_family(prompt_n, entities_map)

    if task_family == "register_payment" and contains_any(prompt_n, ORDER_WORDS) and contains_any(prompt_n, CREATE_WORDS):
        task_family = "order_to_invoice"
        actions = FAMILY_RULES["order_to_invoice"].actions
        confidence = 0.72
        risk_flags.append("payment_order_conflict_resolved_to_order_to_invoice")

    return TaskSpec(
        language=language,
        task_family=task_family,  # type: ignore[arg-type]
        intent=_intent(prompt_n),  # type: ignore[arg-type]
        entities=entities,
        actions=actions,
        requires_payment=task_family == "register_payment",
        confidence=confidence,
        risk_flags=risk_flags,
        prompt=prompt,
    )
