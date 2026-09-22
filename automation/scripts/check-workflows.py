#!/usr/bin/env python3
"""Check the n8n workflow exports for the invariants in AGENTS.md.

Every check here exists because the defect it looks for reached main at least
once. There are deliberately only six. See --help for what each one covers.

Standard library only: no dependencies, no network, no n8n API key.
"""
import argparse
import glob
import json
import os
import re
import sys

CHECKS = """\
1. No disabled nodes. A node with "disabled": true fails. An export taken
   while nodes were switched off for a test once landed on main and silently
   disabled six Slack nodes in the record of truth.

2. No truncated expressions. n8n ends a {{ }} expression at the first }}, so a
   nested object literal has to space its closing braces as } }. Inside every
   {{ ... }} segment of every string parameter starting with =, more { than }
   means the terminator swallowed one and the rest of the expression is gone.
   validate_workflow does not catch this.

3. Every applicant-facing email is gated. The node feeding each emailSend must
   be a Code node whose source reads DRY_RUN. That render step is the only
   thing keeping mail off real applicants.

4. Form answers reach the reviewer. Every key of the responses object built by
   'Record submission' (form-ose.json) must appear in the label list of
   'Render submission thread reply' (form-ose.json) and of
   'Render summary request' (summary.json). A key missing from a label list is
   dropped silently, so an answer the applicant gave never reaches the people
   deciding. A key in MODEL_FREE_KEYS is the other way round in the second
   list: it has to reach a person, so the Slack list carries it, and it must
   not reach a model, so the summary list is checked for its absence.

5. The data table is referenced the same way everywhere. Every data table node
   must point at ose_applications in name mode. A half-updated reference fails
   silently rather than loudly.

6. Every workflow pins its timezone. A workflow with no timezone in its
   settings inherits GENERIC_TIMEZONE and drifts away from the others if that
   ever changes.

7. The form's labels are what reads them. n8n keys a form answer on the
   field's label, so three things break in silence when a label or a dropdown
   option is reworded in the editor. An expression reading
   $('Form page N').item.json['label'] returns undefined. A Switch comparing
   an answer to an option string stops matching and every applicant takes the
   fallback branch, which is the wrong set of questions rather than an error.
   The prefill link the apply 3 emails build from FORM_FIELD stops filling the
   field, and the form still loads, just empty.
"""

DATA_TABLE = "ose_applications"
DATA_TABLE_MODE = "name"
CODE_TYPE = "n8n-nodes-base.code"
EMAIL_TYPE = "n8n-nodes-base.emailSend"

ANSWERS_NODE = "Record submission"
ANSWERS_FILE = "form-ose.json"
# The third element says whether the list is read into a model prompt.
LABEL_NODES = [("form-ose.json", "Render submission thread reply", False),
               ("summary.json", "Render summary request", True)]
# Answers that are not about the application. Only public project material goes to a
# model, and how the applicant found the form is not that, so these keys stay out of
# every label list that feeds one.
MODEL_FREE_KEYS = {"form_feedback"}

FORM_FILE = "form-ose.json"
FORM_TYPES = ("n8n-nodes-base.formTrigger", "n8n-nodes-base.form")
# The first page is the trigger, so the nodes right after it read its answers as
# input['label'] rather than through $('Form page 1').
FORM_TRIGGER_TYPE = "n8n-nodes-base.formTrigger"
# A quoted JS string. The labels carry apostrophes ("Your collective's Open
# Collective URL"), so a pattern that ends at the first quote of either kind
# reads half a label, or misses the reference and checks nothing at all.
QUOTED = r"""(?P<q%s>['"])(?P<%s>(?:\\.|(?!(?P=q%s)).)*)(?P=q%s)"""
NODE_REF = re.compile(r"\$\(\s*" + QUOTED % ("n", "node", "n", "n") + r"\s*\)"
                      r"(?:\.item|\.first\(\)|\.last\(\))?\.json"
                      r"\[\s*" + QUOTED % ("l", "label", "l", "l") + r"\s*\]")
INPUT_REF = re.compile(r"input\[\s*" + QUOTED % ("l", "label", "l", "l") + r"\s*\]")
FORM_FIELD_CONST = re.compile(r"FORM_FIELD\s*=\s*" + QUOTED % ("l", "label", "l", "l"))


def display(path):
    """The path as a reader of the CI log would type it."""
    try:
        relative = os.path.relpath(path)
    except ValueError:
        return path
    return path if relative.startswith("..") else relative


class Workflow:
    def __init__(self, path, doc):
        self.path = display(path)
        self.file = os.path.basename(path)
        self.doc = doc
        self.nodes = doc.get("nodes") or []
        self.by_name = {n.get("name"): n for n in self.nodes}

    def predecessors(self, name):
        """Names of the nodes whose main output feeds `name`."""
        found = []
        for source, outputs in (self.doc.get("connections") or {}).items():
            for branches in (outputs.get("main") or []):
                for link in (branches or []):
                    if link.get("node") == name and source not in found:
                        found.append(source)
        return found


def fail(problems, workflow, node, problem):
    problems.append("%s: %s: %s" % (workflow.path, node, problem))


# --- check 1 ---------------------------------------------------------------

def check_disabled(workflow, problems):
    for node in workflow.nodes:
        if node.get("disabled") is True:
            fail(problems, workflow, node.get("name"),
                 "node is disabled; an export taken with nodes switched off is "
                 "not the record of truth")


# --- check 2 ---------------------------------------------------------------

def strings_in(value, path):
    """Every string in a parameter tree, with a dotted path to it."""
    if isinstance(value, dict):
        for key, item in value.items():
            for found in strings_in(item, path + [str(key)]):
                yield found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            for found in strings_in(item, path + [str(index)]):
                yield found
    elif isinstance(value, str):
        yield ".".join(path), value


def check_truncated_expressions(workflow, problems):
    for node in workflow.nodes:
        for where, text in strings_in(node.get("parameters") or {}, []):
            if not text.startswith("="):
                continue
            cursor = 0
            while True:
                start = text.find("{{", cursor)
                if start < 0:
                    break
                end = text.find("}}", start + 2)
                if end < 0:
                    fail(problems, workflow, node.get("name"),
                         "parameter %s has a {{ with no closing }}" % where)
                    break
                body = text[start + 2:end]
                if body.count("{") > body.count("}"):
                    fail(problems, workflow, node.get("name"),
                         "parameter %s is truncated: the expression %s... "
                         "closes at the first }} of a nested object literal; "
                         "space the closing braces as } }"
                         % (where, body.strip()[:60]))
                cursor = end + 2


# --- check 3 ---------------------------------------------------------------

def check_dry_run_gate(workflow, problems):
    for node in workflow.nodes:
        if node.get("type") != EMAIL_TYPE:
            continue
        name = node.get("name")
        sources = workflow.predecessors(name)
        if not sources:
            fail(problems, workflow, name,
                 "email node has no incoming connection, so nothing checks DRY_RUN")
            continue
        for source in sources:
            render = workflow.by_name.get(source)
            if render is None:
                fail(problems, workflow, name,
                     "the node feeding it, %r, is not in the export" % source)
            elif render.get("type") != CODE_TYPE:
                fail(problems, workflow, name,
                     "the node feeding it, %r, is a %s, not a Code node that "
                     "checks DRY_RUN" % (source, render.get("type")))
            elif "DRY_RUN" not in (render.get("parameters") or {}).get("jsCode", ""):
                fail(problems, workflow, name,
                     "the Code node feeding it, %r, never reads DRY_RUN, so "
                     "this can mail a real applicant during a dry run" % source)


# --- check 4 ---------------------------------------------------------------

def balanced_span(text, open_at):
    """The index just past the brace matching the one at open_at, or -1.

    Tracks quotes so a brace inside a string literal does not shift the depth.
    """
    depth = 0
    quote = None
    index = open_at
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return -1


def top_level_keys(body):
    """Keys of an object literal body, ignoring anything nested inside it."""
    keys = []
    depth = 0
    quote = None
    segment_start = 0
    segments = []
    index = 0
    while index < len(body):
        char = body[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif char == "," and depth == 0:
            segments.append(body[segment_start:index])
            segment_start = index + 1
        index += 1
    segments.append(body[segment_start:])
    for segment in segments:
        match = re.match(r"\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*:", segment)
        if match:
            keys.append(match.group(1))
    return keys


def answer_keys(workflow, problems):
    """The keys of the responses object built by Record submission."""
    node = workflow.by_name.get(ANSWERS_NODE)
    if node is None:
        fail(problems, workflow, ANSWERS_NODE,
             "node is missing, so the form answer keys cannot be checked")
        return None
    columns = ((node.get("parameters") or {}).get("columns") or {}).get("value") or {}
    expression = columns.get("answers")
    if not isinstance(expression, str):
        fail(problems, workflow, ANSWERS_NODE,
             "no answers expression, so the form answer keys cannot be checked")
        return None
    match = re.search(r"responses\s*:\s*\{", expression)
    if not match:
        fail(problems, workflow, ANSWERS_NODE,
             "the answers expression builds no responses object")
        return None
    open_at = match.end() - 1
    close_at = balanced_span(expression, open_at)
    if close_at < 0:
        fail(problems, workflow, ANSWERS_NODE,
             "the responses object in the answers expression is not balanced")
        return None
    keys = top_level_keys(expression[open_at + 1:close_at - 1])
    if not keys:
        fail(problems, workflow, ANSWERS_NODE,
             "the responses object in the answers expression has no keys")
        return None
    return keys


def label_keys(workflow, node_name, problems):
    """The keys of the hardcoded LABELS list of a render Code node."""
    node = workflow.by_name.get(node_name)
    if node is None:
        fail(problems, workflow, node_name,
             "node is missing, so the form answer labels cannot be checked")
        return None
    code = (node.get("parameters") or {}).get("jsCode")
    if not isinstance(code, str):
        fail(problems, workflow, node_name, "node has no source to read labels from")
        return None
    match = re.search(r"LABELS\s*=\s*\[", code)
    if not match:
        fail(problems, workflow, node_name, "node has no LABELS list")
        return None
    return re.findall(r"\[\s*'([^']+)'\s*,", code[match.end():])


def check_answer_labels(workflows, problems, complete):
    """Check 4 spans two exports, so it only runs when both are in the set.

    A partial run (one file named on the command line) skips it and says so; a
    full run over the export directory treats a missing export as a failure.
    """
    form = workflows.get(ANSWERS_FILE)
    if form is None:
        if not complete:
            print("skipped check 4: %s is not among the files checked" % ANSWERS_FILE)
            return
        problems.append("%s: %s: export is missing, so the form answer keys "
                        "cannot be checked" % (ANSWERS_FILE, ANSWERS_NODE))
        return
    keys = answer_keys(form, problems)
    if keys is None:
        return
    for filename, node_name, feeds_model in LABEL_NODES:
        workflow = workflows.get(filename)
        if workflow is None:
            if not complete:
                print("skipped check 4 for %s: it is not among the files checked"
                      % filename)
                continue
            problems.append("%s: %s: export is missing, so the form answer "
                            "labels cannot be checked" % (filename, node_name))
            continue
        labels = label_keys(workflow, node_name, problems)
        if labels is None:
            continue
        for key in keys:
            if feeds_model and key in MODEL_FREE_KEYS:
                if key in labels:
                    fail(problems, workflow, node_name,
                         "label list carries the answer key %r, which is not about the "
                         "application and must not reach a model" % key)
                continue
            if key not in labels:
                fail(problems, workflow, node_name,
                     "label list is missing the answer key %r, which %s records; "
                     "the answer is dropped silently" % (key, ANSWERS_NODE))


# --- check 5 ---------------------------------------------------------------

def check_data_table_reference(workflow, problems):
    for node in workflow.nodes:
        if "datatable" not in (node.get("type") or "").lower():
            continue
        name = node.get("name")
        reference = (node.get("parameters") or {}).get("dataTableId")
        if not isinstance(reference, dict):
            fail(problems, workflow, name, "data table node has no dataTableId")
            continue
        mode = reference.get("mode")
        value = reference.get("value")
        if mode != DATA_TABLE_MODE or value != DATA_TABLE:
            fail(problems, workflow, name,
                 "references the data table as %r in %r mode; every data table "
                 "node must use %r in %r mode"
                 % (value, mode, DATA_TABLE, DATA_TABLE_MODE))


# --- check 6 ---------------------------------------------------------------

def check_timezone(workflow, problems):
    timezone = (workflow.doc.get("settings") or {}).get("timezone")
    if not isinstance(timezone, str) or not timezone.strip():
        fail(problems, workflow, "(workflow settings)",
             "no timezone is pinned, so this workflow inherits GENERIC_TIMEZONE "
             "and drifts away from the others when that changes")


# --- check 7 ---------------------------------------------------------------

def form_fields(workflow):
    """Every form node's labels, each mapped to the options it offers.

    A field with no option list maps to an empty set, which means the check on
    compared strings has nothing to compare against and skips it.
    """
    pages = {}
    for node in workflow.nodes:
        if node.get("type") not in FORM_TYPES:
            continue
        fields = {}
        values = ((node.get("parameters") or {}).get("formFields") or {}).get("values") or []
        for field in values:
            label = field.get("fieldLabel")
            if not isinstance(label, str):
                continue
            options = (field.get("fieldOptions") or {}).get("values") or []
            fields[label] = {o.get("option") for o in options if isinstance(o.get("option"), str)}
        pages[node.get("name")] = fields
    return pages


def trigger_fields(workflow):
    """The labels of the form trigger, which is page 1."""
    for node in workflow.nodes:
        if node.get("type") == FORM_TRIGGER_TYPE:
            return form_fields(workflow).get(node.get("name")) or {}
    return {}


def check_form_label_reads(workflow, pages, problems):
    """Every $('Form page N').item.json['label'] names a field that page has."""
    for node in workflow.nodes:
        for where, text in strings_in(node.get("parameters") or {}, []):
            for match in NODE_REF.finditer(text):
                page, label = match.group("node"), match.group("label")
                if page not in pages or label in pages[page]:
                    continue
                fail(problems, workflow, node.get("name"),
                     "parameter %s reads %r from %r, which has no such field. "
                     "The answer comes back undefined. Its fields are: %s"
                     % (where, label, page, ", ".join(sorted(pages[page])) or "(none)"))


def check_form_option_matches(workflow, pages, problems):
    """Every string a Switch or If compares an answer to is an option of that field."""
    for node in workflow.nodes:
        if node.get("type") not in ("n8n-nodes-base.switch", "n8n-nodes-base.if"):
            continue
        parameters = node.get("parameters") or {}
        for rule in ((parameters.get("rules") or {}).get("values") or []) + [parameters]:
            for condition in ((rule.get("conditions") or {}).get("conditions") or []):
                left = condition.get("leftValue")
                right = condition.get("rightValue")
                if not isinstance(left, str) or not isinstance(right, str) or not right:
                    continue
                match = NODE_REF.search(left)
                if match is None:
                    continue
                page, label = match.group("node"), match.group("label")
                options = pages.get(page, {}).get(label)
                if not options or right in options:
                    continue
                fail(problems, workflow, node.get("name"),
                     "compares %r of %r against %r, which is not one of its "
                     "options. Nothing matches, so every applicant takes the "
                     "fallback branch. Its options are: %s"
                     % (label, page, right, ", ".join(sorted(options))))


def check_form_prefill_field(workflows, problems, complete):
    """The label the apply 3 emails prefill is a field the form trigger has.

    Spans two exports the way check 4 does, so a partial run skips it.
    """
    form = workflows.get(FORM_FILE)
    if form is None:
        if not complete:
            print("skipped check 7 for the prefill label: %s is not among the "
                  "files checked" % FORM_FILE)
            return
        problems.append("%s: (form pages): export is missing, so the prefill "
                        "label cannot be checked" % FORM_FILE)
        return
    labels = trigger_fields(form)
    if not labels:
        problems.append("%s: (form trigger): no form trigger with fields, so "
                        "the prefill label cannot be checked" % FORM_FILE)
        return
    # Every Code node is scanned rather than a fixed list, so a new email that
    # builds the same link is covered the day it is added.
    seen = 0
    for workflow in workflows.values():
        for node in workflow.nodes:
            code = (node.get("parameters") or {}).get("jsCode")
            if not isinstance(code, str):
                continue
            for match in FORM_FIELD_CONST.finditer(code):
                seen += 1
                label = match.group("label")
                if label in labels:
                    continue
                fail(problems, workflow, node.get("name"),
                     "builds a prefill link for %r, which is not a field on the "
                     "form's first page. The link still opens, with the field "
                     "empty. Its fields are: %s"
                     % (label, ", ".join(sorted(labels))))
    if seen:
        return
    # Nothing to compare means the invitation stopped prefilling, or the
    # export that sends it was not among the files checked. Neither is a pass.
    if complete:
        problems.append("%s: (prefill label): no FORM_FIELD constant in any "
                        "export, so no email prefills the form link"
                        % FORM_FILE)
    else:
        print("skipped check 7 for the prefill label: no export among the "
              "files checked builds the link")


def check_form_direct_reads(workflow, problems):
    """A Code node fed by the form trigger reads input['label'] off page 1.

    Only the nodes the trigger feeds are checked. Further down the workflow
    `input` is whatever the node before it returned, not the form answers.
    """
    trigger = None
    for node in workflow.nodes:
        if node.get("type") == FORM_TRIGGER_TYPE:
            trigger = node.get("name")
            break
    if trigger is None:
        return
    labels = trigger_fields(workflow)
    if not labels:
        return
    for node in workflow.nodes:
        name = node.get("name")
        if node.get("type") != CODE_TYPE or trigger not in workflow.predecessors(name):
            continue
        code = (node.get("parameters") or {}).get("jsCode") or ""
        for match in INPUT_REF.finditer(code):
            label = match.group("label")
            if label in labels:
                continue
            fail(problems, workflow, name,
                 "reads %r off %r, which has no such field. The answer comes "
                 "back undefined. Its fields are: %s"
                 % (label, trigger, ", ".join(sorted(labels))))


# --- driver ----------------------------------------------------------------

def default_directory():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "n8n")


def collect(paths):
    files = []
    for path in paths:
        if os.path.isdir(path):
            files.extend(sorted(glob.glob(os.path.join(path, "*.json"))))
        else:
            files.append(path)
    return files


def main(argv):
    parser = argparse.ArgumentParser(
        prog="check-workflows.py",
        description=__doc__,
        epilog=CHECKS,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", metavar="PATH",
                        help="workflow exports or a directory of them "
                             "(default: automation/n8n)")
    args = parser.parse_args(argv)

    complete = not args.paths or any(os.path.isdir(path) for path in args.paths)
    files = collect(args.paths or [default_directory()])
    if not files:
        print("check-workflows: no workflow exports found", file=sys.stderr)
        return 2

    workflows = {}
    problems = []
    for path in files:
        try:
            with open(path) as handle:
                doc = json.load(handle)
        except (OSError, ValueError) as error:
            print("check-workflows: %s: %s" % (path, error), file=sys.stderr)
            return 2
        workflow = Workflow(path, doc)
        workflows[workflow.file] = workflow
        check_disabled(workflow, problems)
        check_truncated_expressions(workflow, problems)
        check_dry_run_gate(workflow, problems)
        check_data_table_reference(workflow, problems)
        check_timezone(workflow, problems)
        pages = form_fields(workflow)
        check_form_label_reads(workflow, pages, problems)
        check_form_option_matches(workflow, pages, problems)
        check_form_direct_reads(workflow, problems)

    check_answer_labels(workflows, problems, complete)
    check_form_prefill_field(workflows, problems, complete)

    for problem in problems:
        print(problem)
    if problems:
        print("\n%d problem(s) in %d workflow export(s)."
              % (len(problems), len(files)))
        return 1
    print("%d workflow export(s) pass all seven checks." % len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
