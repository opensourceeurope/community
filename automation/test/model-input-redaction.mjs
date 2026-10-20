// automation/test/model-input-redaction.mjs
// Runs the two Code nodes that build a model request, Build review request in apply 2 and
// Render summary request in apply 5, straight from the exports, on rows whose free text
// carries email addresses. Every address must be gone from the request, and the address
// columns on the row must never appear in it. A clone URL and a package version, which look
// like addresses and are not, must survive.
//   node automation/test/model-input-redaction.mjs
//
// It runs the node source under plain node, so it proves what the code does with these
// inputs and nothing about the n8n sandbox. Both nodes use only ECMAScript built-ins.
import { readFile } from 'node:fs/promises'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const exports = join(here, '..', 'n8n')

const nodeCode = async (file, name) => {
  const doc = JSON.parse(await readFile(join(exports, file), 'utf8'))
  const node = doc.nodes.find((n) => n.name === name)
  if (!node) throw new Error(`${file}: no node named ${name}`)
  return node.parameters.jsCode
}

const run = (code, rows) => {
  const $input = { all: () => rows.map((json) => ({ json })) }
  const $env = { AI_MODEL: 'test-model' }
  return new Function('$input', '$env', code)($input, $env)
}

// The address columns hold values no free text contains, so finding one in a request can
// only mean the column itself was read.
const COLUMNS = {
  contact_email: 'column-contact@columns.example',
  applicant_email: 'column-applicant@columns.example',
  alternative_admin_name: 'Columnar Secondadmin',
  alternative_admin_email: 'column-admin@columns.example',
}

const PLANTED = [
  'maintainer@project.example',
  'first.last+tag@sub.domain.example.org',
  'security (at) project [dot] example',
  'press [at] project [dot] co [dot] uk',
]

const KEPT = [
  'git@codeberg.org:someone/project.git',
  'left-pad@1.3.0',
  'https://social.example/@someone',
]

const freeText = `Write to ${PLANTED[0]} or ${PLANTED[1]}. Reports go to ${PLANTED[2]}, `
  + `press to ${PLANTED[3]}. Clone ${KEPT[0]}, install ${KEPT[1]}, follow ${KEPT[2]}.`

const answers = {
  applicant_type: 'Open source project',
  repository_url: 'https://codeberg.org/someone/project',
  licence: 'MIT',
  open_development: freeText,
  activities: freeText,
  notes: freeText,
  expected_expenses: freeText,
}

const summaryRow = {
  ...COLUMNS,
  slug: 'example-project',
  collective_name: 'Example project',
  description: freeText,
  repo_url: 'https://codeberg.org/someone/project',
  readme: `# Example\n\n${freeText}\n`,
  applicant_kind: 'project',
  answers: JSON.stringify({ responses: answers }),
}

const reviewRow = {
  ...COLUMNS,
  slug: 'example-project',
  org: 'OSE',
  collective_name: 'Example project',
  description: freeText,
  website_url: 'https://project.example',
  target_kind: 'repo',
  repo_url: 'https://codeberg.org/someone/project',
  licence_spdx: 'MIT',
  readme: `# Example\n\n${freeText}\n`,
  long_description: freeText,
  application_message: freeText,
}

const failures = []
const check = (label, items) => {
  if (!Array.isArray(items) || items.length !== 1) {
    failures.push(`${label}: expected one item, got ${JSON.stringify(items)}`)
    return
  }
  const request = JSON.stringify(items[0].json.request)
  for (const address of [...PLANTED, ...Object.values(COLUMNS)]) {
    if (request.includes(address)) failures.push(`${label}: request still contains ${address}`)
  }
  if (/[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}/.test(request.replaceAll(KEPT[0], ''))) {
    failures.push(`${label}: request still contains something shaped like an address`)
  }
  for (const kept of KEPT) {
    if (!request.includes(kept)) failures.push(`${label}: ${kept} was removed and should not be`)
  }
  if (!request.includes('[email address removed]')) {
    failures.push(`${label}: no redaction marker, so the planted text never reached the request`)
  }
}

check('apply 2 Build review request',
  run(await nodeCode('review.json', 'Build review request'), [reviewRow]))
check('apply 5 Render summary request',
  run(await nodeCode('summary.json', 'Render summary request'), [summaryRow]))

if (failures.length) {
  for (const failure of failures) console.error(failure)
  process.exit(1)
}
console.log('model-input-redaction: both model requests carry no email address')
