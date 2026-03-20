from __future__ import annotations

from workflows.base import Workflow
from workflows.create_credit_note import CreateCreditNoteWorkflow
from workflows.create_department import CreateDepartmentWorkflow
from workflows.create_customer import CreateCustomerWorkflow
from workflows.create_employee import CreateEmployeeWorkflow
from workflows.create_invoice import CreateInvoiceWorkflow
from workflows.create_product import CreateProductWorkflow
from workflows.create_project import CreateProjectWorkflow
from workflows.order_to_invoice import OrderToInvoiceWorkflow
from workflows.register_payment import RegisterPaymentWorkflow
from workflows.salary_transaction import SalaryTransactionWorkflow

WORKFLOW_REGISTRY: dict[str, Workflow] = {
    "create_customer": CreateCustomerWorkflow(),
    "create_employee": CreateEmployeeWorkflow(),
    "create_department": CreateDepartmentWorkflow(),
    "create_credit_note": CreateCreditNoteWorkflow(),
    "create_product": CreateProductWorkflow(),
    "create_project": CreateProjectWorkflow(),
    "order_to_invoice": OrderToInvoiceWorkflow(),
    "create_invoice": CreateInvoiceWorkflow(),
    "register_payment": RegisterPaymentWorkflow(),
    "salary_transaction": SalaryTransactionWorkflow(),
}


def get_workflow(task_family: str) -> Workflow | None:
    return WORKFLOW_REGISTRY.get(task_family)
