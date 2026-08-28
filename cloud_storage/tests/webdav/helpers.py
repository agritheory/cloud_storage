# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe


def file_name_at(file_name, folder="Home", is_folder=False):
	filters = {"file_name": file_name, "folder": folder}
	if is_folder:
		filters["is_folder"] = 1
	return frappe.db.get_value("File", filters, "name")


def put_file(dav_request, track_files, dav_path, content, folder="Home"):
	resp = dav_request("PUT", f"/dav/{dav_path.lstrip('/')}", data=content)
	assert resp.status_code in (200, 201, 204)
	name = file_name_at(dav_path.rsplit("/", 1)[-1], folder)
	track_files(name)
	return name


def make_folder(dav_request, track_files, dav_path, folder="Home"):
	resp = dav_request("MKCOL", f"/dav/{dav_path.lstrip('/')}")
	assert resp.status_code in (200, 201)
	name = file_name_at(dav_path.rsplit("/", 1)[-1], folder, is_folder=True)
	track_files(name)
	return name


def dav_move(dav_request, src, dest):
	return dav_request(
		"MOVE", src, headers={"Destination": f"http://localhost/dav/{dest.lstrip('/')}"}
	)


def make_file(file_name, content, folder="Home", is_private=1):
	return frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"content": content,
			"is_private": is_private,
			"folder": folder,
		}
	).insert()
