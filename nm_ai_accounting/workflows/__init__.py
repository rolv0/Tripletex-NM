from .base import Workflow
from .create_department import CreateDepartmentWorkflow
from .create_customer import CreateCustomerWorkflow
from .create_employee import CreateEmployeeWorkflow
from .create_invoice import CreateInvoiceWorkflow
from .create_product import CreateProductWorkflow
from .create_project import CreateProjectWorkflow
from .order_to_invoice import OrderToInvoiceWorkflow
from .register_payment import RegisterPaymentWorkflow
from .salary_transaction import SalaryTransactionWorkflow

__all__ = [
    "Workflow",
    "CreateDepartmentWorkflow",
    "CreateCustomerWorkflow",
    "CreateEmployeeWorkflow",
    "CreateInvoiceWorkflow",
    "CreateProductWorkflow",
    "CreateProjectWorkflow",
    "OrderToInvoiceWorkflow",
    "RegisterPaymentWorkflow",
    "SalaryTransactionWorkflow",
]
