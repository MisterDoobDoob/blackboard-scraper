import os
import re
import importlib
import curses
import requests
import subprocess
import sys
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from common_mime import common_mime
import course_links as course_links_module
import headers as headers_module

visited_urls = set()
check_headers_only = False
BLACKBOARD_BASE_URL = "https://edimension.sutd.edu.sg"
BLACKBOARD_API_BASE_URL = f"{BLACKBOARD_BASE_URL}/learn/api/v1"


def select_items(title, items, all_label="All", previews=None):
	def draw_menu(screen):
		curses.curs_set(0)
		curses.start_color()
		curses.use_default_colors()
		curses.init_pair(1, curses.COLOR_WHITE, -1)
		cursor = 0
		top = 0

		while True:
			selected_all = selected == {all_label}
			screen.erase()
			height, width = screen.getmaxyx()
			visible_rows = max(1, height - 4)
			if cursor < top:
				top = cursor
			elif cursor >= top + visible_rows:
				top = cursor - visible_rows + 1

			screen.addnstr(0, 0, title, width - 1, curses.A_BOLD)
			screen.addnstr(
				1,
				0,
				"Arrow keys: move | Space: select | Enter: confirm | Q: cancel",
				width - 1,
			)

			rows = [all_label] + list(items)
			for row, item in enumerate(rows[top:top + visible_rows], start=2):
				index = top + row - 2
				is_selected = item in selected
				marker = "[x]" if is_selected else "[ ]"
				attribute = curses.A_REVERSE if index == cursor else curses.A_NORMAL
				prefix = f"{marker} {item}"
				screen.addnstr(row, 0, prefix, width - 1, attribute)
				preview = (previews or {}).get(item)
				if preview and len(prefix) < width - 1:
					screen.addnstr(
						row,
						len(prefix),
						f"  ({preview})",
						width - len(prefix) - 1,
						curses.color_pair(1) | curses.A_DIM,
					)

			screen.refresh()
			key = screen.getch()
			if key in (ord("q"), ord("Q"), 27):
				raise KeyboardInterrupt
			if key in (curses.KEY_UP, ord("k")):
				cursor = max(0, cursor - 1)
			elif key in (curses.KEY_DOWN, ord("j")):
				if cursor == 0 and selected_all:
					selected.clear()
				cursor = min(len(rows) - 1, cursor + 1)
			elif key in (curses.KEY_ENTER, 10, 13):
				if selected_all:
					return None
				chosen = [item for item in items if item in selected]
				if not chosen and cursor > 0:
					return [items[cursor - 1]]
				return chosen
			elif key == ord(" "):
				item = rows[cursor]
				if item == all_label:
					selected.clear()
					selected.add(all_label)
				else:
					if selected_all:
						selected.clear()
					if item in selected:
						selected.remove(item)
					else:
						selected.add(item)
					if not selected:
						selected.add(all_label)

	selected = {all_label}
	return curses.wrapper(draw_menu)


def choose_courses(course_links):
	terms = list(course_links)
	term_previews = {
		term: ", ".join(
			" ".join(course.split()[-3:]) for course in list(course_links[term])[:2]
		)
		for term in terms
	}
	selected_terms = select_items(
		"Select terms to download",
		terms,
		"All terms",
		term_previews,
	)
	if selected_terms is None:
		selected_terms = terms

	selected_courses = {}
	for term in selected_terms:
		courses = course_links[term]
		chosen = select_items(
			f"Select courses in {term}",
			list(courses),
			"All courses",
		)
		selected_courses[term] = list(courses) if chosen is None else chosen

	return selected_courses


def content_is_folder(content):
	details = content.get("contentDetail") or {}
	return any(
		isinstance(detail, dict) and detail.get("isFolder")
		for detail in details.values()
	)


def content_children(course_id, content_id):
	response = requests.get(
		f"{BLACKBOARD_API_BASE_URL}/courses/{course_id}/contents/{content_id}/children",
		headers={
			**headers_module.headers,
			"Accept": "application/json",
		},
		cookies=headers_module.cookies,
		params={"limit": 1000},
		timeout=30,
	)
	response.raise_for_status()
	return response.json().get("results", [])


def content_links(content):
	body = content.get("body") or {}
	markup = body.get("displayText") or body.get("rawText") or ""
	soup = BeautifulSoup(markup, "html.parser")
	return [
		(link.get("href"), " ".join(link.get_text().split()))
		for link in soup.find_all("a", href=True)
	]


def download_course_directory(url, cur_path):
	parsed_url = urlparse(url)
	parts = parsed_url.path.split("/")
	try:
		course_index = parts.index("courses")
		course_id = parts[course_index + 1]
	except (ValueError, IndexError):
		return False

	root_response = requests.get(
		f"{BLACKBOARD_API_BASE_URL}/courses/{course_id}/contents/root",
		headers={
			**headers_module.headers,
			"Accept": "application/json",
		},
		cookies=headers_module.cookies,
		timeout=30,
	)
	if root_response.status_code >= 400:
		if (
			root_response.status_code == 400
			and "Ultra courses only" in root_response.text
		):
			legacy_url = (
				f"{BLACKBOARD_BASE_URL}/webapps/blackboard/execute/"
				f"modulepage/view?course_id={course_id}"
			)
			download_content(legacy_url, cur_path, ignore_course_menu=False)
			return True
		print(
			f"Content directory unavailable for {course_id}: "
			f"HTTP {root_response.status_code}"
		)
		return False
	root = root_response.json()
	queue = [(root["id"], cur_path)]
	seen_content_ids = set()

	while queue:
		content_id, content_path = queue.pop(0)
		if content_id in seen_content_ids:
			continue
		seen_content_ids.add(content_id)

		try:
			children = content_children(course_id, content_id)
		except requests.RequestException as error:
			print(f"Content directory request failed for {content_id}: {error}")
			continue

		for content in children:
			name = re.sub(r'[/\\:*?\”"<>|]', "", " ".join(
				(content.get("title") or "Unnamed").split()
			)).rstrip(". ") or "Unnamed"
			item_path = os.path.join(content_path, name)
			is_folder = content_is_folder(content)
			print(f"DIRECTORY {'DIR' if is_folder else 'FILE'}: {item_path}")

			if is_folder:
				queue.append((content["id"], item_path))
				continue

			if not check_headers_only:
				os.makedirs(item_path, exist_ok=True)
				body = content.get("body") or {}
				markup = body.get("displayText") or body.get("rawText") or ""
				with open(os.path.join(item_path, "page.html"), "w", encoding="utf-8") as file:
					file.write(markup)

			for href, link_name in content_links(content):
				absolute_url = urljoin(BLACKBOARD_BASE_URL, href)
				if "/bbcswebdav" in urlparse(absolute_url).path:
					download_file(absolute_url, content_path, link_name or name)

	return True

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

	if check_headers_only:
		response = requests.head(
			url,
			headers=headers_module.headers,
			cookies=headers_module.cookies,
			allow_redirects=True,
			timeout=30,
		)
		interesting_headers = {
			key: value
			for key, value in response.headers.items()
			if key.lower() in {
				"etag",
				"last-modified",
				"content-length",
				"content-type",
				"accept-ranges",
				"content-range",
			}
		}
		print(f"HEADER CHECK {response.status_code} {url}")
		print(interesting_headers or "No useful file metadata returned")
		return

	response = requests.get(
		url,
		headers=headers_module.headers,
		cookies=headers_module.cookies,
	)

	content_type = response.headers.get('content-type', '')

	if content_type.startswith('application'):
		extension = common_mime[content_type.split(';', 1)[0]]
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
	global check_headers_only
	arguments = sys.argv[1:]
	check_headers_only = "--check-headers" in arguments
	arguments = [argument for argument in arguments if argument != "--check-headers"]
	path = arguments[0] if arguments else 'University'
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

	course_links = course_links_module.course_links
	if any(isinstance(courses, str) for courses in course_links.values()):
		course_links = {"Uncategorized": course_links}

	if check_headers_only:
		selected_courses = {
			term: list(courses) if isinstance(courses, dict) else [term]
			for term, courses in course_links.items()
		}
	else:
		try:
			selected_courses = choose_courses(course_links)
		except KeyboardInterrupt:
			print("Download cancelled.")
			return

	for term, course_names in selected_courses.items():
		courses = course_links[term]
		if isinstance(courses, str):
			courses = {term: courses}
			term = ""

		for name in course_names:
			link = courses[name]
			print(
				f"Checking headers for {name}..."
				if check_headers_only
				else f"Downloading {name}..."
			)
			course_path = os.path.join(path, term, name) if term else os.path.join(path, name)
			if not download_course_directory(link, course_path):
				download_content(link, course_path, ignore_course_menu=False)


if __name__ == "__main__":
	main()
