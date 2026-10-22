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


def load_state(state_file):
    """Load last check state"""
    if os.path.exists(state_file):
        with open(state_file, "r") as f:
            return json.load(f)
    return {}


def save_state(state, state_file):
    """Save current state"""
    state_dir = os.path.dirname(os.path.abspath(state_file))
    os.makedirs(state_dir, exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def file_exists(file_path):
    """Check if file exists on disk"""
    return os.path.isfile(file_path)


def clean_state(state):
    """Remove entries from state where files no longer exist"""
    cleaned_state = {}
    removed_count = 0

    for key, value in state.items():
        # Skip module-only keys (they don't have file paths)
        if "_" in key and not key.count("_") > 2:
            cleaned_state[key] = value
            continue

        # For file keys, check if they contain actual file tracking info
        if isinstance(value, dict) and "path" in value:
            if file_exists(value["path"]):
                cleaned_state[key] = value
            else:
                removed_count += 1
                print(f"  🗑️  Removed missing file from state: {value['path']}")
        else:
            cleaned_state[key] = value

    if removed_count > 0:
        print(f"📊 Cleaned {removed_count} missing files from state\n")

    return cleaned_state


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
        default="last_check.json",
        help="JSON file used to track downloads (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30,
        help="HTTP timeout in seconds (default: %(default)s)",
    )
    return parser


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

    print("✅ Authentication successful!")
    print("🔍 Checking for new Moodle content...")

    # Create download directory
    os.makedirs(args.download_dir, exist_ok=True)

    # Load previous state
    state = load_state(args.state_file)

    # Clean state - remove entries for deleted files
    state = clean_state(state)

    is_first_run = len(state) == 0

    if is_first_run:
        print("📦 First run - downloading all content")

    new_items = []
    redownloaded_items = []
    try:
        courses = get_enrolled_courses(moodle_url, token, args.timeout)
    except (requests.RequestException, RuntimeError, KeyError, ValueError) as error:
        print(f"❌ Failed to fetch enrolled courses: {error}")
        return 1

    for course in courses:
        course_id = course["id"]
        course_name = course["fullname"]
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

                    # Check if this is new
                    module_key = f"{course_id}_{module_id}"
                    is_new = module_key not in state

                    if is_new or is_first_run:
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

                                # Check if file is new or missing
                                file_key = f"{module_key}_{file_name}"
                                file_in_state = file_key in state
                                file_exists_on_disk = file_exists(file_path)

                                # Download if: new file OR file was deleted
                                should_download = (
                                    not file_in_state
                                    or not file_exists_on_disk
                                    or is_first_run
                                )

                                if should_download:
                                    # Determine if this is a redownload
                                    if file_in_state and not file_exists_on_disk:
                                        print(
                                            f"  🔄 Re-downloading deleted file: {file_name}"
                                        )
                                        redownloaded_items.append(file_name)
                                    else:
                                        print(f"  ⬇️  Downloading: {file_name}")

                                    if download_file(
                                        file_url, file_path, token, args.timeout
                                    ):
                                        state[file_key] = {
                                            "path": file_path,
                                            "downloaded": datetime.now().isoformat(),
                                            "course": course_name,
                                            "module": module_name,
                                        }
                                        print(f"  ✅ Downloaded to {file_path}")
                                    else:
                                        print(f"  ❌ Failed to download")
                                else:
                                    # File exists, just verify it's still there
                                    pass

                    # Mark module as seen
                    if module_key not in state:
                        state[module_key] = datetime.now().isoformat()

    # Save state
    save_state(state, args.state_file)

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
