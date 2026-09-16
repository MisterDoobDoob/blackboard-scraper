import os
import re
import importlib
import requests
import subprocess
import sys
from bs4 import BeautifulSoup
from common_mime import common_mime
import course_links as course_links_module
import headers as headers_module

visited_urls = set()

def download_content(url, cur_path, ignore_course_menu=True, verbose=True):

	if url in visited_urls:
		return

	if verbose:
		print(cur_path)

	os.makedirs(cur_path, exist_ok=True)

	visited_urls.add(url)

	response = requests.get(url, headers=headers_module.headers,
							cookies=headers_module.cookies, allow_redirects=True)

	soup = BeautifulSoup(response.content, 'html.parser')

	with open(os.path.join(cur_path, 'page.html'), "w", errors='ignore') as f:
		f.write(str(soup))

	if ignore_course_menu:
		soup = soup.find("div", {"id": "content"})

	if soup is None:
		return


	for link in soup.find_all('a'):
		if link.has_attr('href'):
			href = link['href']
		else:
			continue
		
		name = " ".join(link.get_text().split())
		name = re.sub(r"[/\\:*?\”\"<>|]", '', name)
		
		if href.startswith('/webapps/blackboard/content/listContent'):
			if href.endswith('logout'):
				continue
			download_content(
				f"https://edimension.sutd.edu.sg{href}", os.path.join(cur_path, name))

		if href.startswith('/bbcswebdav'):
			download_file(f"https://edimension.sutd.edu.sg{href}", cur_path, name)

		if href.startswith('https://edimension.sutd.edu.sg/bbcswebdav'):
			download_file(href, cur_path, name)


def download_file(url, path, name):
	if url in visited_urls:
		return

	visited_urls.add(url)

	response = requests.get(
		url,
		headers=headers_module.headers,
		cookies=headers_module.cookies,
	)

	content_type = response.headers.get('content-type')

	if content_type.startswith('application'):
		extension = common_mime[content_type]
		if os.path.exists(os.path.join(path, f'{name}{extension}')):
			for i in range(1, 100):
				if os.path.exists(os.path.join(path, f'{name} ({i}){extension}')):
					continue

				name = f'{name} ({i})'
				break

		
		print(f'{path}/{name}{extension}')

		with open(os.path.join(path, f'{name}{extension}'), 'wb') as f:
			f.write(response.content)


def main():
	path = sys.argv[1] if len(sys.argv) > 1 else 'University'
	try:
		subprocess.run(
			[sys.executable, os.path.join(os.path.dirname(__file__), 'getCourseLinks.py')],
			check=True,
		)
	except subprocess.CalledProcessError:
		print(
			'Course refresh failed. Follow the steps in the README to update your headers.py',
			file=sys.stderr,
		)
		sys.exit(1)

	importlib.reload(course_links_module)
	importlib.reload(headers_module)

	for term, courses in course_links_module.course_links.items():
		if isinstance(courses, str):
			courses = {term: courses}
			term = ""

		for name, link in courses.items():
			print(f'Downloading {name}...')
			course_path = os.path.join(path, term, name) if term else os.path.join(path, name)
			download_content(link, course_path, ignore_course_menu=False)


if __name__ == "__main__":
	main()
