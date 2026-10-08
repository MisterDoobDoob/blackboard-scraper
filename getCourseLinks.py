import json
import re
import shlex
import sys
from pathlib import Path

import requests

try:
    from headers import cookies, headers
except (ImportError, AttributeError):
    cookies = {}
    headers = {}


API_BASE_URL = "https://edimension.sutd.edu.sg/learn/api/v1"
OUTPUT_FILE = Path(__file__).with_name("course_links.py")
HEADERS_FILE = Path(__file__).with_name("headers.py")
SHOW_PARSED_CREDENTIALS = True


def validate_credentials():
    if not headers or not cookies:
        raise RuntimeError(
            "headers.py is empty. Follow the steps in the README to update your headers.py " \
            "with fresh cookies and request headers from eDimension." \
        )


def safe_name(value):
    value = re.sub(r'[/\\:*?"<>|]', "", " ".join(value.split()))
    return value.rstrip(". ") or "Unnamed"


def request_headers():
    return {
        **headers,
        "Accept": "application/json",
        "Referer": "https://edimension.sutd.edu.sg/",
    }


def parse_cookie_string(cookie_string):
    # Cookie sections are semicolon-separated, while values may contain '='.
    cookies = {}
    for item in cookie_string.split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name:
            cookies[name] = value
    return cookies


def normalize_ansi_c_quotes(curl_command):
    def decode_ansi_c_quote(match):
        value = re.sub(
            r"\\([0-7]{1,3})",
            lambda octal: chr(int(octal.group(1), 8)),
            match.group(1),
        )
        return "'" + value + "'"

    return re.sub(r"\$'((?:\\.|[^'])*)'", decode_ansi_c_quote, curl_command)


def parse_curl(curl_command):
    """Parse browser-exported cURL headers and cookies."""
    arguments = shlex.split(
        normalize_ansi_c_quotes(curl_command).replace("\\\n", " ")
    )
    parsed_headers = {}
    parsed_cookies = {}
    index = 0

    while index < len(arguments):
        argument = arguments[index]
        value = None
        # Header sections support -H, --header, and their equals forms.
        if argument in ("-H", "--header"):
            index += 1
            if index >= len(arguments):
                raise ValueError(f"Missing value after {argument}.")
            value = arguments[index]
        elif argument.startswith(("--header=", "-H")):
            value = argument.split("=", 1)[1] if "=" in argument else argument[2:]

        if value is not None:
            name, separator, header_value = value.partition(":")
            if not separator or not name.strip():
                raise ValueError(f"Invalid header argument: {value!r}")
            name = name.strip()
            if name.lower() == "cookie":
                parsed_cookies.update(parse_cookie_string(header_value))
            else:
                parsed_headers[name] = header_value.strip()
        # Cookie sections support -b/--cookie and Cookie headers.
        elif argument in ("-b", "--cookie"):
            index += 1
            if index >= len(arguments):
                raise ValueError(f"Missing value after {argument}.")
            parsed_cookies.update(parse_cookie_string(arguments[index]))
        elif argument.startswith(("--cookie=", "-b=")):
            parsed_cookies.update(parse_cookie_string(argument.split("=", 1)[1]))

        index += 1

    if not parsed_headers or not parsed_cookies:
        raise ValueError("The cURL request must contain at least one header and one cookie.")
    return parsed_headers, parsed_cookies


def write_credentials(parsed_headers, parsed_cookies):
    existing = HEADERS_FILE.read_text(encoding="utf-8") if HEADERS_FILE.exists() else ""
    active_credentials = re.search(r"(?m)^(?:cookies|headers)\s*=\s*\{", existing)
    comments = existing[:active_credentials.start()] if active_credentials else existing
    generated = (
        "\n".join(
            [
                "cookies = " + repr(parsed_cookies),
                "",
                "headers = " + repr(parsed_headers),
                "",
            ]
        )
    )
    HEADERS_FILE.write_text(comments.rstrip() + "\n" + generated, encoding="utf-8")


def update_credentials_from_curl():
    global cookies, headers

    print("Paste the authenticated cURL request, then press Enter:")
    curl_lines = [input()]
    while curl_lines[-1].rstrip().endswith("\\"):
        curl_lines[-1] = curl_lines[-1].rstrip()[:-1]
        curl_lines.append(input())
    curl_command = " ".join(curl_lines)
    parsed_headers, parsed_cookies = parse_curl(curl_command)
    write_credentials(parsed_headers, parsed_cookies)
    headers = parsed_headers
    cookies = parsed_cookies
    if SHOW_PARSED_CREDENTIALS:
        print(f"Parsed headers: {parsed_headers}")
        print(f"Parsed cookies: {parsed_cookies}")
    else:
        print(
            f"Parsed {len(parsed_headers)} headers and "
            f"{len(parsed_cookies)} cookies."
        )


def request_with_credential_refresh(session, url):
    for attempt in range(2):
        response = session.get(
            url,
            headers=request_headers(),
            cookies=cookies,
            timeout=30,
        )
        if response.status_code not in (401, 403) or attempt == 1:
            return response
        print(f"Blackboard rejected the request ({response.status_code}).")
        update_credentials_from_curl()
    return response


def fetch_user_id(session):
    response = request_with_credential_refresh(session, f"{API_BASE_URL}/users/me")
    if response.status_code in (401, 403):
        raise RuntimeError(
            f"Blackboard rejected the request ({response.status_code}). \n"
            "Cookies are outdated; follow the steps in the README to update \n"
            "headers.py with fresh cookies and request headers from eDimension."
        )
    response.raise_for_status()
    user_id = response.json().get("id")
    if not user_id:
        raise RuntimeError("Blackboard did not return a user ID from /users/me.")
    return user_id


def fetch_courses():
    validate_credentials()
    with requests.Session() as session:
        user_id = fetch_user_id(session)
        api_url = (
            f"{API_BASE_URL}/users/{user_id}/memberships"
        "?expand=course.effectiveAvailability,course.permissions,courseRole"
        "&includeCount=true&limit=10000"
        )
        response = request_with_credential_refresh(session, api_url)
        if response.status_code in (401, 403):
            raise RuntimeError(
                f"Blackboard rejected the request ({response.status_code}). \n"
                "Cookies are outdated; follow the steps in the README to update headers.py\n"
                "with fresh cookies and request headers from eDimension."
            )
        response.raise_for_status()
        payload = response.json()

    courses = {}
    for membership in payload.get("results", []):
        course = membership.get("course") or {}
        term = ((course.get("term") or {}).get("description") or {}).get("rawText")
        display_name = course.get("displayName")
        link = course.get("externalAccessUrl")

        if not term or not display_name or not link:
            continue

        term = safe_name(term)
        display_name = safe_name(display_name)
        courses.setdefault(term, {})[display_name] = link

    return courses


def write_course_links(courses):
    contents = "# Generated by getCourseLinks.py.\ncourse_links = "
    contents += json.dumps(courses, indent=4, ensure_ascii=True)
    contents += "\n"
    OUTPUT_FILE.write_text(contents, encoding="utf-8")


if __name__ == "__main__":
    try:
        course_links = fetch_courses()
        write_course_links(course_links)
        course_count = sum(len(courses) for courses in course_links.values())
        print(f"Wrote {course_count} courses to {OUTPUT_FILE}")
    except (requests.RequestException, RuntimeError, ValueError) as error:
        print(f"Error fetching Blackboard courses: {error}", file=sys.stderr)
        sys.exit(1)