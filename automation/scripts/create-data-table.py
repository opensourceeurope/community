#!/usr/bin/env python3
"""Create the ose_applications data table on an n8n instance, or add the
columns it is missing.

The column list is read from the table in automation/docs/data-tables.md, so
the document stays the one place that defines the table. A new instance needs
the table before any workflow can run, because every data table node finds it
by name.

The script only adds. It never removes, renames or retypes a column, and a
column on the instance that the document does not list is reported and left
alone. Running it twice changes nothing the second time.

Reads the instance URL from N8N_API_URL and the API key from N8N_API_KEY, or
from the file named by N8N_API_KEY_FILE. The key needs the dataTable:read,
dataTable:create and dataTableColumn:create scopes.

  create-data-table.py --dry-run   report what it would create, change nothing
  create-data-table.py             create the table or the missing columns
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

TABLE = "ose_applications"
DOC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "data-tables.md")
TYPES = {"String": "string", "Number": "number", "Date": "date", "Boolean": "boolean"}
# "String (JSON)" is a String column whose readers parse it, as the doc says.
ROW = re.compile(r"^\| `([a-z][a-z0-9_]*)` \| (String|Number|Date|Boolean)(?: \(JSON\))? \|")


class ApiError(Exception):
    pass


def documented_columns(path):
    """The columns of the first table under '## `ose_applications`', in order."""
    columns, in_section = [], False
    with open(path) as handle:
        for line in handle:
            if line.startswith("## "):
                if in_section:
                    break
                in_section = line.strip() == "## `%s`" % TABLE
                continue
            match = ROW.match(line) if in_section else None
            if match:
                columns.append({"name": match.group(1), "type": TYPES[match.group(2)]})
    if not columns:
        sys.exit("error: no columns found for %s in %s" % (TABLE, path))
    return columns


def read_key():
    key = os.environ.get("N8N_API_KEY", "")
    if key:
        return key
    key_file = os.environ.get("N8N_API_KEY_FILE", "")
    if not key_file:
        sys.exit("error: no API key. Set N8N_API_KEY or N8N_API_KEY_FILE")
    try:
        with open(os.path.expanduser(key_file)) as handle:
            key = handle.read().strip()
    except OSError as error:
        sys.exit("error: cannot read the API key file: %s" % error)
    if not key:
        sys.exit("error: the API key file %s is empty" % key_file)
    return key


def call(base, key, method, path, body=None):
    request = urllib.request.Request(base + path, method=method)
    request.add_header("X-N8N-API-KEY", key)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, data, timeout=60) as response:
            text = response.read().decode("utf-8")
            return json.loads(text) if text else None
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise ApiError("HTTP %d on %s %s: %s" % (error.code, method, path, detail))
    except urllib.error.URLError as error:
        raise ApiError("cannot reach %s: %s" % (base, error.reason))


def items(response):
    """A list endpoint answers either a bare list or {data: [...]}."""
    if isinstance(response, list):
        return response
    return (response or {}).get("data") or []


def find_table(base, key):
    # n8n refuses a filter with any reserved character left unescaped.
    name_filter = urllib.parse.quote(json.dumps({"name": TABLE}, separators=(",", ":")), safe="")
    query = "limit=100&filter=" + name_filter
    for table in items(call(base, key, "GET", "/api/v1/data-tables?" + query)):
        if table.get("name") == TABLE:
            return table
    return None


def main():
    parser = argparse.ArgumentParser(
        prog="create-data-table.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be created and change nothing")
    args = parser.parse_args()

    base = os.environ.get("N8N_API_URL", "").rstrip("/")
    if not base:
        sys.exit("error: set N8N_API_URL, for example https://{ADMIN_HOSTNAME}")
    key = read_key()
    wanted = documented_columns(DOC)
    mode = " (dry run)" if args.dry_run else ""

    try:
        table = find_table(base, key)
        if table is None:
            print("%s does not exist on %s%s. It gets %d columns from %s"
                  % (TABLE, base, mode, len(wanted), os.path.relpath(DOC)))
            if not args.dry_run:
                created = call(base, key, "POST", "/api/v1/data-tables",
                               {"name": TABLE, "columns": wanted})
                print("created %s, id %s" % (TABLE, (created or {}).get("id")))
            return 0

        existing = items(call(base, key, "GET",
                              "/api/v1/data-tables/%s/columns" % table["id"]))
        names = {column.get("name") for column in existing}
        missing = [column for column in wanted if column["name"] not in names]
        extra = sorted(names - {column["name"] for column in wanted})
        print("%s exists on %s with %d columns%s" % (TABLE, base, len(existing), mode))
        for column in missing:
            print("  missing  %s (%s)" % (column["name"], column["type"]))
            if not args.dry_run:
                call(base, key, "POST",
                     "/api/v1/data-tables/%s/columns" % table["id"], column)
                print("  added    %s" % column["name"])
        for name in extra:
            print("  not in the document, left alone: %s" % name)
        types = {column.get("name"): column.get("type") for column in existing}
        for column in wanted:
            have = types.get(column["name"])
            if have and have != column["type"]:
                print("  type differs, left alone: %s is %s, the document says %s"
                      % (column["name"], have, column["type"]))
        if not missing:
            print("every documented column is present")
        return 0
    except ApiError as error:
        print("error: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
