"""Download and synchronize Moodle course content."""

import argparse
import getpass
import json
import os
import sys
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import requests


def get_token(moodle_url, username, password, timeout):
    """Get authentication token from Moodle"""
    token_url = f"{moodle_url}/login/token.php"
    params = {
        "username": username,
        "password": password,
        "service": "moodle_mobile_app",
    }

    try:
        response = requests.get(token_url, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()

        if "token" in data:
            return data["token"]
        elif "error" in data:
            print(f"❌ Login error: {data['error']}")
            if "errorcode" in data:
                print(f"   Error code: {data['errorcode']}")
            return None
        else:
            print(f"❌ Unexpected response: {data}")
            return None

    except (requests.RequestException, ValueError) as e:
        print(f"❌ Failed to get token: {e}")
        return None


def call_moodle_api(moodle_url, token, function, params, timeout):
    """Call Moodle Web Service API"""
    url = f"{moodle_url}/webservice/rest/server.php"
    request_params = {
        **params,
        "wstoken": token,
        "wsfunction": function,
        "moodlewsrestformat": "json",
    }
    response = requests.get(url, params=request_params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and "exception" in data:
        raise RuntimeError(data.get("message", "Moodle API request failed"))
    return data


def get_enrolled_courses(moodle_url, token, timeout):
    """Get all enrolled courses"""
    # First get your user ID
    user_info = call_moodle_api(
        moodle_url, token, "core_webservice_get_site_info", {}, timeout
    )
    userid = user_info["userid"]

    # Get enrolled courses
    courses = call_moodle_api(
        moodle_url,
        token,
        "core_enrol_get_users_courses",
        {"userid": userid},
        timeout,
    )
    return courses


def get_course_contents(moodle_url, token, course_id, timeout):
    """Get all contents of a course"""
    contents = call_moodle_api(
        moodle_url,
        token,
        "core_course_get_contents",
        {"courseid": course_id},
        timeout,
    )
    return contents


def download_file(file_url, file_path, token, timeout):
    """Download file from Moodle"""
    try:
        response = requests.get(
            file_url, params={"token": token}, stream=True, timeout=timeout
        )
    except requests.RequestException as error:
        print(f"  ❌ Download failed: {error}")
        return False
    if response.status_code == 200:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    print(f"  ❌ Download failed ({response.status_code}): {file_url}")
    return False


def load_download_map(map_file):
    """Load the download map, including legacy state files."""
    if not os.path.exists(map_file):
        return {"included_courses": [], "folders": [], "files": {}}
    try:
        with open(map_file, "r", encoding="utf-8") as map_handle:
            data = json.load(map_handle)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Failed to read download map: {error}") from error
    if (
        isinstance(data, dict)
        and isinstance(data.get("files"), dict)
        and isinstance(data.get("included_courses"), list)
    ):
        data.setdefault("folders", [])
        return data
    if not isinstance(data, dict):
        raise ValueError("Download map must contain a JSON object.")
    # Preserve maps created by earlier versions.
    files = {
        value["path"]: value
        for value in data.values()
        if isinstance(value, dict) and "path" in value
    }
    return {"included_courses": [], "folders": [], "files": files}


def save_download_map(download_map, map_file):
    """Save the download map."""
    map_dir = os.path.dirname(os.path.abspath(map_file))
    os.makedirs(map_dir, exist_ok=True)
    with open(map_file, "w", encoding="utf-8") as map_handle:
        json.dump(download_map, map_handle, indent=2)


def clean_download_map(download_map):
    """Remove files from the map that no longer exist on disk."""
    existing_files = {}
    removed_count = 0
    for path, metadata in download_map["files"].items():
        if file_exists(path):
            existing_files[path] = metadata
        else:
            removed_count += 1
    download_map["files"] = existing_files
    if removed_count:
        print(f"📊 Found {removed_count} missing file(s) in the download map\n")
    return download_map


def add_to_download_map(download_map, course, file_path, file_url, section_name):
    """Record a course, folder, and file in the download map."""
    course_entry = {
        "id": course["id"],
        "name": course["fullname"],
    }
    if course_entry not in download_map["included_courses"]:
        download_map["included_courses"].append(course_entry)
    folder_path = os.path.dirname(file_path)
    if folder_path not in download_map["folders"]:
        download_map["folders"].append(folder_path)
    download_map["files"][file_path] = {
        "url": file_url,
        "course_id": course["id"],
        "course": course["fullname"],
        "section": section_name,
        "downloaded": file_exists(file_path),
        "updated": datetime.now().isoformat(),
    }


def file_exists(file_path):
    """Check if file exists on disk."""
    return os.path.isfile(file_path)


def save_state(state, state_file):
    """Backward-compatible alias for older callers."""
    state_dir = os.path.dirname(os.path.abspath(state_file))
    os.makedirs(state_dir, exist_ok=True)
    save_download_map(state, state_file)


def safe_name(value):
    """Return a filesystem-friendly course name."""
    return "".join(c for c in value if c.isalnum() or c in (" ", "-", "_")).strip()


def normalize_url(url):
    """Normalize a Moodle site URL without changing its path."""
    parsed = urlsplit(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Moodle URL must be an absolute http:// or https:// URL")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def build_parser():
    parser = argparse.ArgumentParser(
        description="Synchronize materials from all enrolled Moodle courses."
    )
    parser.add_argument(
        "--credentials",
        default="cred.json",
        help="JSON credentials file (default: %(default)s)",
    )
    parser.add_argument("--url", help="Moodle site URL; overrides the credentials file")
    parser.add_argument("--username", help="Moodle username; overrides the credentials file")
    parser.add_argument(
        "--password",
        help="Moodle password (omit to enter it securely at the prompt)",
    )
    parser.add_argument(
        "--download-dir",
        default="moodle_downloads",
        help="Directory for downloaded files (default: %(default)s)",
    )
    parser.add_argument(
        "--state-file",
        default="download-map.json",
        help="JSON download map file (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30,
        help="HTTP timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--course",
        action="append",
        dest="courses",
        metavar="ID_OR_NAME",
        help="Only synchronize this course; repeat for multiple courses",
    )
    parser.add_argument(
        "--list-courses",
        action="store_true",
        help="List enrolled courses and exit without downloading",
    )
    parser.add_argument(
        "--configure-courses",
        action="store_true",
        help="Interactively select courses and save the selection",
    )
    parser.add_argument(
        "--course-config",
        default="courses.json",
        help="JSON course selection file (default: %(default)s)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Check the download map and download only missing files",
    )
    return parser


def load_course_selection(course_config):
    """Load saved course IDs, if a course selection file exists."""
    if not os.path.exists(course_config):
        return None
    try:
        with open(course_config, "r", encoding="utf-8") as selection_file:
            selection = json.load(selection_file)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Failed to read course configuration: {error}") from error
    if not isinstance(selection, dict) or not isinstance(selection.get("course_ids"), list):
        raise ValueError("Course configuration must contain a course_ids list.")
    return {str(course_id) for course_id in selection["course_ids"]}


def save_course_selection(course_config, courses):
    """Save selected course IDs and names."""
    config_dir = os.path.dirname(os.path.abspath(course_config))
    os.makedirs(config_dir, exist_ok=True)
    selection = {
        "course_ids": [course["id"] for course in courses],
        "courses": [
            {"id": course["id"], "name": course["fullname"]} for course in courses
        ],
    }
    with open(course_config, "w", encoding="utf-8") as selection_file:
        json.dump(selection, selection_file, indent=2)


def select_courses_interactively(courses):
    """Prompt for one or more course numbers."""
    print("\n📚 Enrolled courses:")
    for index, course in enumerate(courses, start=1):
        print(f"  {index}. {course['fullname']} (ID: {course['id']})")
    print("\nEnter course numbers separated by commas, or 'all' for every course.")

    while True:
        selection = input("Courses to include: ").strip().lower()
        if selection == "all":
            return courses
        try:
            indexes = [int(value.strip()) for value in selection.split(",")]
        except ValueError:
            indexes = []
        if indexes and all(1 <= index <= len(courses) for index in indexes):
            selected_indexes = set(indexes)
            return [courses[index - 1] for index in sorted(selected_indexes)]
        print("❌ Enter valid course numbers separated by commas, or 'all'.")


def main(argv=None):
    args = build_parser().parse_args(argv)

    credentials = {}
    if os.path.exists(args.credentials):
        try:
            with open(args.credentials, "r", encoding="utf-8") as credentials_file:
                credentials = json.load(credentials_file)
        except (OSError, json.JSONDecodeError) as error:
            print(f"❌ Failed to read credentials file: {error}", file=sys.stderr)
            return 2
        if not isinstance(credentials, dict):
            print("❌ Credentials file must contain a JSON object.", file=sys.stderr)
            return 2

    moodle_url_value = args.url or credentials.get("url")
    username = args.username or credentials.get("username")
    password = args.password or credentials.get("password")
    if not moodle_url_value or not username:
        print(
            "❌ Provide --url and --username, or add them to the credentials file.",
            file=sys.stderr,
        )
        return 2

    try:
        moodle_url = normalize_url(moodle_url_value)
    except ValueError as error:
        print(f"❌ {error}", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("❌ --timeout must be greater than zero", file=sys.stderr)
        return 2

    password = password or getpass.getpass("Moodle password: ")

    # Get token
    print("🔑 Authenticating...")
    token = get_token(moodle_url, username, password, args.timeout)

    if not token:
        print("❌ Failed to authenticate. Please check your credentials.")
        return 1

    try:
        courses = get_enrolled_courses(moodle_url, token, args.timeout)
    except (requests.RequestException, RuntimeError, KeyError, ValueError) as error:
        print(f"❌ Failed to fetch enrolled courses: {error}")
        return 1

    if args.list_courses:
        print("\n📚 Enrolled courses:")
        for course in courses:
            print(f"  {course['id']}: {course['fullname']}")
        return 0

    if args.configure_courses:
        try:
            selected_courses = select_courses_interactively(courses)
            save_course_selection(args.course_config, selected_courses)
        except (EOFError, KeyboardInterrupt):
            print("\n❌ Course configuration canceled.", file=sys.stderr)
            return 130
        except OSError as error:
            print(f"❌ Failed to save course configuration: {error}", file=sys.stderr)
            return 1
        print(
            f"✅ Saved {len(selected_courses)} course(s) to {args.course_config}."
        )
        return 0

    requested_courses = set(args.courses or [])
    if not requested_courses:
        try:
            requested_courses = load_course_selection(args.course_config) or set()
        except ValueError as error:
            print(f"❌ {error}", file=sys.stderr)
            return 2

    if requested_courses:
        selected_courses = [
            course
            for course in courses
            if str(course["id"]) in requested_courses
            or course["fullname"] in requested_courses
        ]
        missing_courses = requested_courses - {
            str(course["id"]) for course in selected_courses
        } - {course["fullname"] for course in selected_courses}
        if missing_courses:
            print(
                "❌ Course not found: " + ", ".join(sorted(missing_courses)),
                file=sys.stderr,
            )
            print(
                "Use --list-courses or --configure-courses to update the selection."
            )
            return 2
        courses = selected_courses

    print("✅ Authentication successful!")
    print("🔍 Checking for new Moodle content...")

    # Create download directory
    os.makedirs(args.download_dir, exist_ok=True)

    try:
        download_map = clean_download_map(load_download_map(args.state_file))
    except ValueError as error:
        print(f"❌ {error}", file=sys.stderr)
        return 2

    if not args.update and not download_map["files"]:
        print("📦 First run - downloading all content")

    new_items = []
    redownloaded_items = []

    for course in courses:
        course_id = course["id"]
        course_name = course["fullname"]
        if {
            "id": course_id,
            "name": course_name,
        } not in download_map["included_courses"]:
            download_map["included_courses"].append(
                {"id": course_id, "name": course_name}
            )
        print(f"\n📚 Course: {course_name}")

        # Get course contents
        try:
            contents = get_course_contents(moodle_url, token, course_id, args.timeout)
        except (requests.RequestException, RuntimeError, ValueError) as error:
            print(f"❌ Failed to fetch course contents: {error}")
            continue

        for section in contents:
            section_name = section["name"]

            if "modules" in section:
                for module in section["modules"]:
                    module_id = module["id"]
                    module_name = module["name"]
                    module_type = module["modname"]

                    if not args.update:
                        new_items.append(
                            {
                                "course": course_name,
                                "section": section_name,
                                "name": module_name,
                                "type": module_type,
                            }
                        )

                    # Download files if available
                    if "contents" in module:
                        for content in module["contents"]:
                            if content["type"] == "file":
                                file_name = content["filename"]
                                file_url = content["fileurl"]

                                # Create safe file path
                                safe_course = safe_name(course_name)
                                file_path = os.path.join(
                                    args.download_dir, safe_course, section_name, file_name
                                )
                                was_tracked = file_path in download_map["files"]
                                add_to_download_map(
                                    download_map,
                                    course,
                                    file_path,
                                    file_url,
                                    section_name,
                                )

                                file_exists_on_disk = file_exists(file_path)
                                should_download = not file_exists_on_disk

                                if should_download:
                                    if was_tracked:
                                        print(
                                            f"  🔄 Re-downloading deleted file: {file_name}"
                                        )
                                        redownloaded_items.append(file_name)
                                    else:
                                        print(f"  ⬇️  Downloading: {file_name}")

                                    if download_file(
                                        file_url, file_path, token, args.timeout
                                    ):
                                        download_map["files"][file_path]["downloaded"] = True
                                        print(f"  ✅ Downloaded to {file_path}")
                                    else:
                                        download_map["files"][file_path]["downloaded"] = False
                                        print(f"  ❌ Failed to download")

    # Save state
    save_download_map(download_map, args.state_file)

    # Summary
    print("\n" + "=" * 50)

    if redownloaded_items:
        print(f"🔄 Re-downloaded {len(redownloaded_items)} deleted files:")
        for item in redownloaded_items:
            print(f"  • {item}")
        print()

    if new_items:
        print(f"📬 Found {len(new_items)} new items:")
        for item in new_items:
            print(f"  • [{item['type']}] {item['name']} in {item['course']}")
    else:
        if not redownloaded_items:
            print("✅ No new content found")

    print(f"\n💾 Downloads saved to: {args.download_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
