# Moodle Content Automator

Moodle Content Automator is a command-line tool that logs in to a Moodle
installation, discovers all courses in which the user is enrolled, and
synchronizes their downloadable course materials to a local directory.

Features include Moodle REST API authentication, enrolled-course discovery,
incremental file synchronization, automatic recovery of deleted downloads,
secure password prompting, credential-file support, configurable download and
state paths, request timeouts, and fully documented CLI options. Parts of the
implementation and documentation were developed with AI assistance.

## Requirements

- Python 3.6 or newer
- The [`requests`](https://docs.python-requests.org/) package
- A Moodle account with access to the Moodle mobile app web service

## Installation

```bash
git clone https://github.com/omar-ezeldin/moodle-scraper.git
cd moodle-scraper
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
```

You can provide settings through the CLI or a local `cred.json` file, which is
ignored by Git.

## Usage

Run a synchronization with the required site and username options:

```bash
python moodle-scraper.py \
  --url https://elearning.example.edu \
  --username your_username
```

For repeated use, copy the example credentials file and edit the copy:

```bash
cp cred.example.json cred.json
chmod 600 cred.json
python moodle-scraper.py
```

The credentials file uses this format:

```json
{
  "url": "https://elearning.example.edu",
  "username": "your_username",
  "password": "your_password"
}
```

Use another credentials file with `--credentials PATH`. CLI options override
matching values from the credentials file, so a password can still be entered
securely with `--password` omitted from the JSON file.

The password is requested through a hidden prompt. For non-interactive
environments, it can be supplied explicitly:

```bash
python moodle-scraper.py \
  --url https://elearning.example.edu \
  --username your_username \
  --password 'your_password' \
  --download-dir ~/Moodle \
  --state-file ~/.local/state/moodle-scraper.json \
  --timeout 60
```

Avoid putting passwords in shell history or process listings when possible;
the secure prompt is recommended for normal use.

### Options

| Option | Required | Default | Description |
| --- | --- | --- | --- |
| `--credentials PATH` | No | `cred.json` | JSON file containing `url`, `username`, and optionally `password` |
| `--url URL` | No* | From credentials file | Absolute Moodle site URL |
| `--username USERNAME` | No* | From credentials file | Moodle username |
| `--password PASSWORD` | No | Secure prompt | Moodle password |
| `--download-dir PATH` | No | `moodle_downloads` | Directory for downloaded materials |
| `--state-file PATH` | No | `download-map.json` | JSON map of included courses, folders, and downloaded files |
| `--timeout SECONDS` | No | `30` | HTTP timeout for each request |
| `--course ID_OR_NAME` | No | All courses | Synchronize only the selected course; repeat for multiple courses |
| `--list-courses` | No | — | List enrolled courses and exit |
| `--configure-courses` | No | — | Interactively select and save courses for future syncs |
| `--course-config PATH` | No | `courses.json` | JSON file storing the saved course selection |
| `--update` | No | — | Check the download map and download only missing files |
| `-h`, `--help` | No | — | Show command help |

`*` Required when the value is not present in the credentials file.

The complete option reference is also available from the tool:

```bash
python moodle-scraper.py --help
```

List available courses before selecting one:

```bash
python moodle-scraper.py --list-courses
```

Select a course by its Moodle ID or exact name:

```bash
python moodle-scraper.py --course 42
python moodle-scraper.py --course "Introduction to Python"
```

Repeat `--course` to synchronize multiple selected courses.

### Saved course selection

Use the interactive configuration command to choose which enrolled courses
should be synchronized by default:

```bash
python moodle-scraper.py --configure-courses
```

The command displays every enrolled course and accepts selections such as
`1,3,5` or `all`. The selected course IDs and names are saved to
`courses.json`, which is ignored by Git. Future runs automatically use that
selection:

```bash
python moodle-scraper.py
```

Use `--course-config PATH` to save or use a different selection file.
Explicit `--course` options override the saved selection, and running without
a saved selection synchronizes all enrolled courses.

### Download map and updates

Each synchronization writes a JSON download map containing the included
courses, destination folders, file paths, Moodle URLs, and course metadata.
The default map is `download-map.json`; use `--state-file PATH` to choose a
different location.

To verify the selected courses and download only files missing from disk:

```bash
python moodle-scraper.py --update
```

The update command never overwrites files that are already present. It also
records newly discovered remote files in the map and downloads them if they are
missing locally.

## Synchronization behavior

On the first run, all file resources exposed by enrolled courses are
downloaded. Later runs download newly discovered files and automatically
restore files that were deleted locally. The state file records file paths and
timestamps, while downloaded files are organized as:

```text
moodle_downloads/
└── Course Name/
    └── Section Name/
        ├── material.pdf
        └── Assignment Name/
            └── task.pdf
```

Files attached to an assignment (shown on the assignment's page, not as a
separate course file) go in a folder named after the assignment, inside its
section. The folder is only created when the assignment has files, so
assignments without attachments leave no empty folders.

The state file and download directory are ignored by Git. Use `--state-file`
to keep separate synchronization histories or to place state outside the
repository.

## License and credits

This project is released under the [MIT License](LICENSE).

- Original project: [`zedsalim/moodle-scraper`](https://github.com/zedsalim/moodle-scraper)
- Enhanced CLI/API implementation: [`omar-ezeldin/moodle-scraper`](https://github.com/omar-ezeldin/moodle-scraper)
