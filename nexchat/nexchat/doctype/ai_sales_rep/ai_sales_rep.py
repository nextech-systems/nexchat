import frappe
from frappe.model.document import Document


class AISalesRep(Document):
    def validate(self):
        if self.pbx_extension:
            self.pbx_extension = self.pbx_extension.strip()

        if self.mobile_number:
            self.mobile_number = self.mobile_number.strip()

        if self.rep_code:
            self.rep_code = self.rep_code.strip().upper()
