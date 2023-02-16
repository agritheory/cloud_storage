import frappe


def test_permissions():
	frappe.set_user("Administrator")
	#creating 2 users
	test_user2=frappe.new_doc("User")#User2
	test_user2.email="support@agritheory.dev"
	test_user2.first_name="Support"
	test_user2.save()
	
	# test user with employee and projects permission-creating those doc
	employee_doc2=frappe.new_doc("Employee") # Employee2
	employee_doc2.first_name='Test Employee2'
	employee_doc2.company = frappe.defaults.get_defaults().get('company')
	employee_doc2.date_of_birth="1997-08-31"
	employee_doc2.date_of_joining="2021-01-08"
	employee_doc2.user_id=test_user2.name
	employee_doc2.state="TN"
	employee_doc2.save()

	#setting permission for user2
	user2 = frappe.get_doc("User", test_user2.name)
	user2.add_roles("Employee")
	user2.add_roles("Projects User")

	test_user1=frappe.new_doc("User")#User 1
	test_user1.email="billing@agritheory.dev"
	test_user1.first_name="Bill"
	test_user1.save()

	#first employee-Purchase Manager,employee and projects permission
	employee_doc1=frappe.new_doc("Employee")#Employee1
	employee_doc1.first_name='Test Employee1'
	employee_doc1.company=frappe.defaults.get_defaults().get('company')
	employee_doc1.date_of_birth="1997-08-30"
	employee_doc1.date_of_joining="2021-01-07"
	employee_doc1.user_id=test_user1.name
	employee_doc1.state="TN"
	employee_doc1.save() 
	
	#setting roles for user1
	user1 = frappe.get_doc("User", test_user1.name)
	user1.add_roles("Purchase Manager")
	user1.add_roles("Employee")
	user1.add_roles("Projects User")

	frappe.set_user("billing@agritheory.dev")#using  User1

	po_record=frappe.new_doc("Purchase Order")#I'm creating PO record so that we can attach file to the same record with user1 for both PO and file
	po_record.supplier=frappe.get_last_doc('Supplier').name
	po_record.company=frappe.defaults.get_defaults().get('company')
	po_record.transaction_date="2021-08-27"
	po_record.owner=employee_doc1.user_id
	po_record.append("items", {
		"item_code": frappe.get_last_doc('Item').name,
		"item_name":"Utilities Bill",
		"schedule_date":"2021-08-28",
		"qty": 5,
		"stock_uom":"Nos",
		"uom":"Nos",
		"conversion_factor":1,
		"rate":float(10000),
		"base_amount":float(10000)
	})
	po_record.save()
	#attching a file to the record--User permission
	file_doc=frappe.new_doc("File")
	file_doc.file_name='test_screenshot'
	file_doc.is_private=1
	file_doc.file_url="/private/files/Screenshot from 2021-08-17 11-08-56.png"
	file_doc.attached_to_doctype="Purchase Order"
	file_doc.attached_to_name=po_record.name
	file_doc.owner=employee_doc1.user_id
	file_doc.save()
	 
	#access the file with second user-and should get error
	frappe.set_user("support@agritheory.dev")
	file_access=frappe.get_doc('File',file_doc.name)
	has_permission_check=CustomFile.has_permission(file_access,ptype=None,user="support@agritheory.dev")
	if has_permission_check== True or has_permission_check==1:
		pass
	else:
		frappe.throw("You don't have permission to access this record")
	#with Purchase order--with same user and record--Role Permission