from __future__ import annotations

from workflows.base import Workflow
from workflows.create_credit_note import CreateCreditNoteWorkflow
from workflows.create_department import CreateDepartmentWorkflow
from workflows.create_customer import CreateCustomerWorkflow
from workflows.create_employee import CreateEmployeeWorkflow
from workflows.create_invoice import CreateInvoiceWorkflow
from workflows.create_product import CreateProductWorkflow
from workflows.create_project import CreateProjectWorkflow
from workflows.create_supplier import CreateSupplierWorkflow
from workflows.create_travel_expense import CreateTravelExpenseWorkflow
from workflows.log_hours import LogHoursWorkflow
from workflows.order_to_invoice import OrderToInvoiceWorkflow
from workflows.register_payment import RegisterPaymentWorkflow
from workflows.salary_transaction import SalaryTransactionWorkflow
from workflows.ledger_correction import LedgerCorrectionWorkflow

WORKFLOW_REGISTRY: dict[str, Workflow] = {
    "create_customer": CreateCustomerWorkflow(),
    "create_supplier": CreateSupplierWorkflow(),
    "create_employee": CreateEmployeeWorkflow(),
    "create_department": CreateDepartmentWorkflow(),
    "create_credit_note": CreateCreditNoteWorkflow(),
    "create_product": CreateProductWorkflow(),
    "create_project": CreateProjectWorkflow(),
    "log_hours": LogHoursWorkflow(),
    "order_to_invoice": OrderToInvoiceWorkflow(),
    "create_invoice": CreateInvoiceWorkflow(),
    "register_payment": RegisterPaymentWorkflow(),
    "salary_transaction": SalaryTransactionWorkflow(),
    "ledger_correction": LedgerCorrectionWorkflow(),
    "create_travel_expense": CreateTravelExpenseWorkflow(),
}


def get_workflow(task_family: str) -> Workflow | None:
    return WORKFLOW_REGISTRY.get(task_family)
