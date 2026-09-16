# Blackboard Scraper Notes

## Repository Files

- `headers.py`: Local Blackboard cookies and request headers. This file contains session credentials and should not be committed or shared.
- `getCourseLinks.py`: Authenticates with the configured session, discovers the current user ID, fetches course memberships, and writes `course_links.py`.
- `course_links.py`: Generated course mapping grouped as `term -> course display name -> course URL`.
- `scrape.py`: Refreshes `course_links.py`, then downloads each course page, linked content pages, and Blackboard WebDAV files.
- `common_mime.py`: Maps HTTP `Content-Type` values to filename extensions for downloaded files.
- `README.md`: Setup and usage instructions.
- `test.js`: Separate timetable extraction script; it is not part of the course scraper flow.

## Blackboard API

The API base URL is:

```text
https://edimension.sutd.edu.sg/learn/api/v1
```

`getCourseLinks.py` first requests the authenticated user's profile:

```text
GET /users/me
```

The response contains the current user's Blackboard ID in `id`, for example `_651325_1`. That ID is used to request memberships:

```text
GET /users/{user_id}/memberships?expand=course.effectiveAvailability,course.permissions,courseRole&includeCount=true&limit=10000
```

Course data is read from each membership using:

- `results[].course.term.description.rawText` for the term folder.
- `results[].course.displayName` for the course folder and mapping key.
- `results[].course.externalAccessUrl` for the course URL.

The API requires valid authenticated cookies and headers from `headers.py`. A `401` or `403` normally means the copied session credentials have expired.

## Runtime Flow

Run:

```text
python3.12 scrape.py
```

`scrape.py` runs `getCourseLinks.py` as a separate process. On success, it reloads the generated course mapping and the current headers module before downloading. Downloaded content is placed under:

```text
University/<term>/<course>/
```

Generated downloads and Python cache files are excluded by `.gitignore`.

## Testing

Run the syntax and whitespace checks:

```text
python3 -m py_compile scrape.py getCourseLinks.py headers.py course_links.py
git diff --check
```

Check editor diagnostics for `scrape.py` and `getCourseLinks.py` after code changes.

To test the authenticated API refresh without downloading course content:

```text
python3 getCourseLinks.py
```

A successful run reports the number of generated courses and updates `course_links.py`. A `401` or `403` indicates that the cookies or headers in `headers.py` need to be refreshed.

To test the complete workflow:

```text
python3.12 scrape.py
```

This refreshes the course list and downloads pages and files under `University/<term>/<course>/`. An optional output directory can be supplied as the first argument.