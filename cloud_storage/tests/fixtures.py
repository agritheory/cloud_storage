# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

SHARED_VIEWER = "pat.viewer@testcompany.test"
OTHER_USER = "sam.manager@testcompany.test"

users = [
	{
		"email": SHARED_VIEWER,
		"first_name": "Pat",
		"last_name": "Viewer",
		"roles": [],
	},
	{
		"email": OTHER_USER,
		"first_name": "Sam",
		"last_name": "Manager",
		"roles": ["System Manager"],
	},
]
