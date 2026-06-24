#https://repo.redwizard.xyz/redwizardrepo/main/

import requests
import os
import shutil
import re

def download_latest(base_url, download_dir, addon_id):
	"""
	Download latest addon zip from Kodi repo using raw XML parsing
	"""
	repo_xml_url = base_url + "/addons.xml"
	
	# Fetch addons.xml
	response = requests.get(repo_xml_url)
	response.raise_for_status()
	xml_text = response.text

	# ✅ Regex to find addon block + version
	# Matches: <addon id="..." version="...">
	pattern = rf'<addon\s+[^>]*id="{re.escape(addon_id)}"[^>]*version="([^"]+)"'

	match = re.search(pattern, xml_text)

	if not match:
		raise ValueError(f"{addon_id} not found")

	version = match.group(1)

	zip_name = f"{addon_id}-{version}.zip"
	zip_url = f"{base_url}{addon_id}/{zip_name}"

	print(f"Downloading: {zip_url}")

	# Download ZIP
	os.makedirs(download_dir, exist_ok=True)
	zip_path = os.path.join(download_dir, zip_name)

	with requests.get(zip_url, stream=True) as r:
		r.raise_for_status()
		with open(zip_path, "wb") as f:
			for chunk in r.iter_content(chunk_size=8192):
				if chunk:
					f.write(chunk)

	print(f"Saved to: {zip_path}")
	return zip_path


def clean_python_artifacts(root_path):
	"""
	Recursively remove __pycache__ directories and Python temp files.
	Works on Windows and Linux.
	Args:
		root_path (str): Path to clean.
	"""
	# File extensions to remove
	temp_extensions = {'.pyc', '.pyo', '.pyd', '.py~'}

	for dirpath, dirnames, filenames in os.walk(root_path, topdown=False):
		# Remove temp files
		for filename in filenames:
			_, ext = os.path.splitext(filename)
			if ext.lower() in temp_extensions:
				full_path = os.path.join(dirpath, filename)
				try:
					os.remove(full_path)
					print(f"Removed file: {full_path}")
				except Exception as e:
					print(f"Failed to remove file {full_path}: {e}")

		# Remove __pycache__ directories (and any empty dirs if desired)
		for dirname in dirnames:
			full_path = os.path.join(dirpath, dirname)

			if dirname == '__pycache__':
				try:
					shutil.rmtree(full_path)
					print(f"Removed directory: {full_path}")
				except Exception as e:
					print(f"Failed to remove directory {full_path}: {e}")
			else:
				# Optional: remove empty directories
				try:
					if not os.listdir(full_path):
						os.rmdir(full_path)
						print(f"Removed empty directory: {full_path}")
				except Exception:
					pass  # ignore non-empty or permission errors


def update_plugin_references(root_path, search_str, replace_str):
	"""
	Recursively scan files and update plugin.video.redlight -> plugin.video.redlight_mod
	only if not already modified.
	Args:
		root_path (str): Root directory to scan
	"""
	target_extensions = {'.py', '.xml', '.txt'}
	#search_str = "plugin.video.redlight"
	#replace_str = "plugin.video.redlight_mod"

	for dirpath, _, filenames in os.walk(root_path):
		for filename in filenames:
			_, ext = os.path.splitext(filename)
			if ext.lower() not in target_extensions:
				continue

			file_path = os.path.join(dirpath, filename)

			try:
				with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
					lines = f.readlines()

				modified = False
				new_lines = []

				for line in lines:
					# Replace only if not already modified
					if search_str in line and replace_str not in line:
						line = line.replace(search_str, replace_str)
						modified = True
					new_lines.append(line)

				# Rewrite file only if changes were made
				if modified:
					with open(file_path, 'w', encoding='utf-8', errors='ignore') as f:
						f.writelines(new_lines)
					print(f"Updated: {file_path}")

			except Exception as e:
				print(f"Failed to process {file_path}: {e}")


#root_path =  r'C:\TEMP\New folder (2)\plugin.video.redlight - Copy'

root_path = r'C:\TEMP\New folder (3)'

clean_python_artifacts(root_path)
exit()

search_str = "plugin.video.redlight"
replace_str = "plugin.video.redlight_mod"

update_plugin_references(root_path, search_str, replace_str)


base_url = "https://repo.redwizard.xyz/redwizardrepo/main/" 

download_dir = r"C:\TEMP\New folder (2)"
addon_id = search_str



download_latest(base_url, download_dir, addon_id)

