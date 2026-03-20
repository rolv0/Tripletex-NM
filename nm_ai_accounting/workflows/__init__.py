from .base import Workflow
from .create_credit_note import CreateCreditNoteWorkflow
from .create_department import CreateDepartmentWorkflow
from .create_customer import CreateCustomerWorkflow
from .create_employee import CreateEmployeeWorkflow
from .create_invoice import CreateInvoiceWorkflow
from .create_product import CreateProductWorkflow
from .create_project import CreateProjectWorkflow
from .create_supplier import CreateSupplierWorkflow
from .create_travel_expense import CreateTravelExpenseWorkflow
from .order_to_invoice import OrderToInvoiceWorkflow
from .register_payment import RegisterPaymentWorkflow
from .salary_transaction import SalaryTransactionWorkflow
from .ledger_correction import LedgerCorrectionWorkflow

__all__ = [
    "Workflow",
    "CreateCreditNoteWorkflow",
    "CreateDepartmentWorkflow",
    "CreateCustomerWorkflow",
    "CreateSupplierWorkflow",
    "CreateEmployeeWorkflow",
    "CreateInvoiceWorkflow",
    "CreateProductWorkflow",
    "CreateProjectWorkflow",
    "CreateTravelExpenseWorkflow",
    "OrderToInvoiceWorkflow",
    "RegisterPaymentWorkflow",
    "SalaryTransactionWorkflow",
    "LedgerCorrectionWorkflow",
]
