from frappe import _dict

source_link = "https://github.com/agritheory/cloud_storage"
headline = "Cloud Storage"
sub_heading = "Save your files in the cloud"

# type: ignore
def get_context(context) -> _dict:
	context.brand_html = "Cloud Storage"
