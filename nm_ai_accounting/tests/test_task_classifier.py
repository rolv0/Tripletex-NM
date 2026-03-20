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


def test_ledger_dimension_prompt_routes_to_ledger_correction():
    prompt = (
        'Cree una dimension contable personalizada "Produktlinje" con los valores '
        '"Standard" y "Basis". Luego registre un asiento en la cuenta 7100 por 43400 NOK.'
    )
    spec = classify_task(prompt, _attachments())
    assert spec.task_family == "ledger_correction"

def test_supplier_prompt_routes_to_create_supplier():
    prompt = "Register the supplier Silveroak Ltd with organization number 943413231."
    spec = classify_task(prompt, _attachments())
    assert spec.task_family == "create_supplier"


def test_portuguese_travel_prompt_routes_to_create_travel_expense():
    prompt = (
        'Registe uma despesa de viagem para Bruno Silva referente a "Conferencia Bodo". '
        'A viagem durou 3 dias com ajudas de custo.'
    )
    spec = classify_task(prompt, _attachments())
    assert spec.task_family == "create_travel_expense"


def test_french_dimension_prompt_routes_to_ledger_correction():
    prompt = (
        'Creez une dimension comptable personnalisee "Prosjekttype" avec les valeurs '
        '"Internt" et "Utvikling". Puis comptabilisez une piece sur le compte 7300 '
        "pour 10150 NOK."
    )
    spec = classify_task(prompt, _attachments())
    assert spec.task_family == "ledger_correction"
