<!-- Copyright (c) 2025, AgriTheory and contributors
For license information, please see license.txt-->

# Cloud Storage Hooks

## Path Generator Hook

The Cloud Storage app provides a hook to customize how file paths are generated in your S3-compatible storage bucket.

`cloud_storage_path_generator`

By default, Cloud Storage uses the File document's name as the S3 object key.
However, you may need to customize this behavior for specific integrations or archival requirements.

### Default Behavior

The default path generator uses the File document's `name` field and respects the `folder` configuration from your site's `site_config.json`:

```python
# Default path structure:
# {folder}/{file.file_name}

# Examples (with folder="documents" in site_config.json):
# - documents/file-1.pdf
# - documents/image-a.png

# Examples (without folder configured):
# - file-1.pdf
# - image-a.png
```

### Configuration

To use a custom path generator, add the following to your custom app's `hooks.py`:

```python
# hooks.py
cloud_storage_path_generator = "my_custom_app.utils.custom_path_generator"
```

### Custom Path Generator Function

Your custom function must accept two parameters and return a string path:

```python
def custom_path_generator(file, folder=None):
    """
    Generate a custom S3 object key for a File document.
    
    Args:
        file (File): The File document instance
        folder (str|None): Optional base folder from Cloud Storage Settings
        
    Returns:
        str: The S3 object key/path
    """
    # Your custom logic here
    return "your/custom/path.pdf"
```

### Example

```python
# my_custom_app/utils.py

def custom_get_file_path(file: File, folder: str | None = None) -> str:
	parent_doctype = file.attached_to_doctype or "No Doctype"

	attached_to_name = ""
	if file.attached_to_name:
		attached_to_name = file.attached_to_name.replace("#", "%23")

	fragments = [
		folder,
		parent_doctype,
		attached_to_name,
		file.file_name.replace("#", "%23"),
	]

	valid_fragments: list[str] = list(filter(None, fragments))
	path = "/".join(valid_fragments)
	return path
```

### Error Handling

If your custom path generator fails, Cloud Storage will:
1. Log the error to the Error Log
2. Fall back to the default path generation strategy
