from __future__ import annotations

from models import Entity, TaskSpec
from parsing.attachment_parser import ParsedAttachment
from parsing.entity_extractor import extract_all_entities
from parsing.language_detector import detect_language
from parsing.prompt_normalizer import normalize_prompt
from utils.text import contains_any

CREATE_WORDS = {"create", "opprett", "lag", "registrer", "register", "crea", "crie", "erstellen", "creez", "registe"}
UPDATE_WORDS = {"update", "oppdater", "endre", "actualizar", "aktualisieren", "modifier"}
DELETE_WORDS = {"delete", "remove", "slett", "fjern", "eliminar", "supprimer", "loschen"}
PAYMENT_WORDS = {"payment", "betaling", "pago", "pagamento", "zahlung", "paiement", "delbetaling"}
INVOICE_WORDS = {"invoice", "faktura", "factura", "fatura", "rechnung", "facture"}
ORDER_WORDS = {"order", "ordre", "pedido", "encomenda", "bestellung", "commande"}
CONVERT_WORDS = {"convert", "konverter", "convierte", "converter", "umwandeln", "convertir"}
PROJECT_WORDS = {"project", "prosjekt", "proyecto", "projet", "projekt"}
PRODUCT_WORDS = {"product", "produkt", "producto", "produit"}
CUSTOMER_WORDS = {"customer", "kunde", "cliente", "client", "kunden"}
SUPPLIER_WORDS = {"supplier", "leverandor", "fornecedor", "fournisseur", "proveedor", "lieferant"}
EMPLOYEE_WORDS = {"employee", "ansatt", "empleado", "mitarbeiter", "salarie"}
DEPARTMENT_WORDS = {"department", "avdeling", "departamento", "departement", "abteilung"}
PAYROLL_WORDS = {"salary", "payroll", "lonn", "loenn", "salaire", "salario", "paie"}
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
}
CREDIT_WORDS = {"credit note", "kreditnota", "gutschrift", "avoir", "nota de credito"}
LEDGER_WORDS = {"ledger", "hovedbok", "asiento", "asiento contable", "buchung", "ecriture"}
DIMENSION_WORDS = {"dimension", "dimensjon", "dimensione", "dimension contable", "dimension comptable"}


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

    has_invoice = contains_any(prompt_n, INVOICE_WORDS)
    has_order = contains_any(prompt_n, ORDER_WORDS)
    has_payment = contains_any(prompt_n, PAYMENT_WORDS)
    has_convert = contains_any(prompt_n, CONVERT_WORDS)
    has_create = contains_any(prompt_n, CREATE_WORDS)

    task_family = "unknown"
    actions: list[str] = []
    confidence = 0.55
    risk_flags: list[str] = []

    if has_order and has_invoice and has_convert:
        task_family = "order_to_invoice"
        actions = ["ensure_customer", "create_order", "create_invoice"]
        confidence = 0.95
    elif has_payment and has_invoice and not (has_order and has_create):
        task_family = "register_payment"
        actions = ["find_invoice", "register_payment"]
        confidence = 0.9
    elif contains_any(prompt_n, CREDIT_WORDS) and has_invoice:
        task_family = "create_credit_note"
        actions = ["find_invoice", "create_credit_note"]
        confidence = 0.9
    elif contains_any(prompt_n, PAYROLL_WORDS):
        task_family = "salary_transaction"
        actions = ["find_employee", "create_salary_transaction"]
        confidence = 0.88
    elif contains_any(prompt_n, TRAVEL_WORDS) and contains_any(prompt_n, DELETE_WORDS):
        task_family = "delete_travel_expense"
        actions = ["find_travel_expense", "delete_travel_expense"]
        confidence = 0.85
    elif contains_any(prompt_n, TRAVEL_WORDS):
        task_family = "create_travel_expense"
        actions = ["find_employee", "create_travel_expense"]
        confidence = 0.85
    elif has_invoice and has_create:
        task_family = "create_invoice"
        actions = ["ensure_customer", "create_invoice"]
        confidence = 0.86
    elif contains_any(prompt_n, PROJECT_WORDS):
        task_family = "create_project"
        actions = ["ensure_customer", "create_or_update_project"]
        confidence = 0.82
    elif contains_any(prompt_n, PRODUCT_WORDS):
        task_family = "create_product"
        actions = ["create_product"]
        confidence = 0.9
    elif contains_any(prompt_n, EMPLOYEE_WORDS):
        task_family = "create_employee"
        actions = ["create_employee"]
        confidence = 0.9
    elif contains_any(prompt_n, DEPARTMENT_WORDS):
        task_family = "create_department"
        actions = ["create_departments"]
        confidence = 0.84
    elif contains_any(prompt_n, SUPPLIER_WORDS):
        task_family = "create_supplier"
        actions = ["create_supplier"]
        confidence = 0.84
    elif contains_any(prompt_n, DIMENSION_WORDS) and contains_any(prompt_n, LEDGER_WORDS):
        task_family = "ledger_correction"
        actions = ["create_accounting_dimension", "create_dimension_values", "register_voucher"]
        confidence = 0.82
    elif contains_any(prompt_n, CUSTOMER_WORDS):
        task_family = "create_customer"
        actions = ["create_customer"]
        confidence = 0.8
    else:
        risk_flags.append("unclassified_prompt")

    if task_family == "register_payment" and has_order and has_create:
        # Explicit guardrail from observed failures.
        task_family = "order_to_invoice"
        actions = ["ensure_customer", "create_order", "create_invoice"]
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
