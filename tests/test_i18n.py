"""Every string the settings panel shows has to exist in Russian.

The rule has been "if you add something, add its translation" for as long as
the panel has been bilingual, and it has been kept by remembering. This checks
it instead: every label and every help line declared in SETTINGS_META, and
every settings tab name, has to appear as a key in i18n.js.

It reads the two files as text rather than running them. A JS parser is not
worth carrying for this, and the declarations are regular enough that finding
them with a pattern is honest - if the shape of SETTINGS_META ever changes,
the count check at the bottom fails loudly rather than the whole file quietly
matching nothing and passing.
"""
import json
import re
import sys

from _harness import Failures

report = Failures("settings translations")

app = open("static/js/app.js", encoding="utf-8").read()
i18n = open("static/js/i18n.js", encoding="utf-8").read()

# label: "..." with an optional help: "..." after it, inside one field entry.
FIELD = re.compile(
    r'\{\s*key:\s*"[^"]+",\s*label:\s*"([^"]+)"(?:[^{}]*?help:\s*"((?:[^"\\]|\\.)*)")?[^{}]*?\}'
)


def unescape(value):
    """The JS string literal as the browser would see it."""
    return json.loads('"' + value + '"')


labels, helps = [], []
for match in FIELD.finditer(app):
    labels.append(match.group(1))
    if match.group(2) is not None:
        helps.append(unescape(match.group(2)))

# The tab names and blurbs, which are declared separately.
for match in re.finditer(r'\{\s*id:\s*"\w+",\s*label:\s*"([^"]+)",\s*blurb:\s*"([^"]+)"\s*\}', app):
    labels.append(match.group(1))
    labels.append(match.group(2))


def translated(text):
    """Is there a key for this string in the translation table?"""
    return json.dumps(text, ensure_ascii=False) + ":" in i18n or f'"{text}":' in i18n


report.section("the file was actually read")
# Without this, a changed SETTINGS_META shape would make every loop below run
# zero times and the whole file would pass while checking nothing.
report.check("field labels were found", len(labels) > 30, True)
report.check("help lines were found", len(helps) > 25, True)

report.section("labels")
missing_labels = sorted({label for label in labels if not translated(label)})
report.check("every settings label has a translation", missing_labels, [])

report.section("help text")
missing_helps = sorted({help_text for help_text in helps if not translated(help_text)})
report.check("every settings help line has a translation",
             [text[:60] + "..." for text in missing_helps], [])

report.section("the newest ones in particular")
for label in ("Decline Team Invites", "Invite Green Pixels"):
    report.check(f"{label} is translated", translated(label), True)
report.check("and no placeholder was left behind", "PLACEHOLDER" in i18n, False)


sys.exit(report.finish())
