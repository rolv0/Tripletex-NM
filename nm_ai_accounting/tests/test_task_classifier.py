from parsing.attachment_parser import ParsedAttachment
from routing.task_classifier import classify_task


def _attachments() -> list[ParsedAttachment]:
    return []


def test_order_to_invoice_is_not_payment():
    prompt = (
        "Crea un pedido para el cliente Río Verde SL (org. nº 937237243) "
        "con dos líneas. Convierte el pedido en factura."
    )
    spec = classify_task(prompt, _attachments())
    assert spec.task_family == "order_to_invoice"
    assert spec.requires_payment is False


def test_payment_prompt_routes_to_register_payment():
    prompt = "Registrer full betaling på faktura 123 for kunden Nord AS."
    spec = classify_task(prompt, _attachments())
    assert spec.task_family == "register_payment"
    assert spec.requires_payment is True


def test_payroll_routes_to_salary_transaction():
    prompt = "Exécutez la paie de Sarah Moreau avec un salaire de base 56900 NOK et bonus 15800 NOK."
    spec = classify_task(prompt, _attachments())
    assert spec.task_family == "salary_transaction"

