// automation/test/review-eval.mjs
// Runs each fixture through the review prompt and checks the verdict against the schema
// and the fixture's expectation.
//   AI_API_KEY=... node automation/test/review-eval.mjs
//
// Validator self-test, no API key needed:
//   node automation/test/review-eval.mjs --self-test
import { readFile, readdir } from 'node:fs/promises'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..')

const schema = JSON.parse(await readFile(join(root, 'prompts/verdict.schema.json'), 'utf8'))

const render = (tpl, vars) =>
  tpl.replace(/\{\{\s*(\w+)\s*\}\}/g, (_, k) => vars[k] || '(not provided)')

// Minimal validator: enough for this one flat schema, no dependency needed.
function validate(obj) {
  const errors = []
  for (const key of schema.required) if (!(key in obj)) errors.push(`missing ${key}`)
  for (const [key, value] of Object.entries(obj)) {
    const rule = schema.properties[key]
    if (!rule) { errors.push(`unexpected ${key}`); continue }
    const actual = typeof value
    let wanted
    if (rule.type === 'number') wanted = 'number'
    else if (rule.type === 'boolean') wanted = 'boolean'
    else if (rule.type === 'string') wanted = 'string'
    else { errors.push(`${key}: schema type "${rule.type}" is not handled by this validator`); continue }
    if (actual !== wanted) { errors.push(`${key}: expected ${wanted}, got ${actual}`); continue }
    if (rule.enum && !rule.enum.includes(value)) errors.push(`${key}: "${value}" not in enum`)
    if (rule.minimum !== undefined && value < rule.minimum) errors.push(`${key}: below minimum`)
    if (rule.maximum !== undefined && value > rule.maximum) errors.push(`${key}: above maximum`)
    if (rule.minLength && value.length < rule.minLength) errors.push(`${key}: shorter than ${rule.minLength}`)
    if (rule.maxLength && value.length > rule.maxLength) errors.push(`${key}: longer than ${rule.maxLength}`)
  }
  return errors
}

// Hand-written cases covering: a valid object, and one case per kind of schema
// violation the validator is supposed to catch. Runs against the real schema
// file, needs no network and no API key.
function selfTest() {
  const validReasoning = 'This reasoning is long enough to satisfy the schema minimum length rule.'
  const validMessage = 'This applicant message is long enough to satisfy the schema minimum length rule.'
  const base = { verdict: 'fits', confidence: 0.8, reasoning: validReasoning, applicant_message: validMessage }

  const cases = [
    {
      name: 'valid verdict',
      input: { ...base },
      expect: (errors) => errors.length === 0,
      describe: 'zero errors',
    },
    {
      name: 'wrong enum value',
      input: { ...base, verdict: 'maybe' },
      expect: (errors) => errors.some((e) => e.includes('not in enum')),
      describe: 'an enum error',
    },
    {
      name: 'missing required field',
      input: (({ reasoning, ...rest }) => rest)(base),
      expect: (errors) => errors.includes('missing reasoning'),
      describe: '"missing reasoning"',
    },
    {
      name: 'out-of-range confidence',
      input: { ...base, confidence: 1.5 },
      expect: (errors) => errors.some((e) => e.includes('above maximum')),
      describe: 'an above-maximum error',
    },
    {
      name: 'wrong type',
      input: { ...base, confidence: '0.8' },
      expect: (errors) => errors.some((e) => e.includes('expected number, got string')),
      describe: 'a type-mismatch error',
    },
    {
      name: 'forbidden extra field',
      input: { ...base, extra_field: true },
      expect: (errors) => errors.includes('unexpected extra_field'),
      describe: '"unexpected extra_field"',
    },
  ]

  let failed = 0
  for (const c of cases) {
    const errors = validate(c.input)
    const ok = c.expect(errors)
    if (!ok) failed++
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${c.name}`)
    if (!ok) console.log(`  expected ${c.describe}, got: ${JSON.stringify(errors)}`)
  }
  console.log(`\n${cases.length - failed}/${cases.length} self-test cases passed`)
  return failed
}

if (process.argv.includes('--self-test')) {
  const failed = selfTest()
  process.exit(failed ? 1 : 0)
}

const BASE = process.env.AI_BASE_URL ?? 'https://api.scaleway.ai'
const MODEL = process.env.AI_MODEL ?? 'mistral-medium-3.5-128b'
const KEY = process.env.AI_API_KEY
if (!KEY) { console.error('AI_API_KEY is not set'); process.exit(2) }

const system = await readFile(join(root, 'prompts/review.system.md'), 'utf8')
const template = await readFile(join(root, 'prompts/review.user.md'), 'utf8')

async function review(input) {
  const res = await fetch(`${BASE}/v1/chat/completions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${KEY}` },
    body: JSON.stringify({
      model: MODEL,
      temperature: 0,
      // Scaleway honoured json_object for mistral-small-3.2 on the live endpoint
      // (2026-09-01), so there is deliberately no parse-retry fallback here.
      // Scaleway lists structured output for mistral-medium-3.5 too. A full run
      // of this eval against it is what proves it, and if parsing breaks, add a
      // fallback then.
      response_format: { type: 'json_object' },
      messages: [
        { role: 'system', content: system },
        { role: 'user', content: render(template, input) },
      ],
    }),
  })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  const body = await res.json()
  return JSON.parse(body.choices[0].message.content)
}

// The register check: applicant_message is pasted verbatim into a real email,
// and the email rules forbid exclamation marks and marketing enthusiasm. The
// fixtures assert the verdict; this asserts the writing.
const BANNED_WORDS = ['great', 'awesome', 'amazing', 'exciting', 'excited', 'love', 'fantastic', 'wonderful']
function toneErrors(msg) {
  const errors = []
  if (msg.includes('!')) errors.push('applicant_message contains an exclamation mark')
  const lower = msg.toLowerCase()
  for (const w of BANNED_WORDS) {
    if (new RegExp(`\\b${w}\\b`).test(lower)) errors.push(`applicant_message contains "${w}"`)
  }
  return errors
}

// The directory is shared with other tests, readme-fetch.json among them, so a file is a
// review case only when it carries the two fields a case needs.
const dir = join(root, 'test/fixtures')
const fixtures = []
for (const file of (await readdir(dir)).filter((f) => f.endsWith('.json')).sort()) {
  const fixture = JSON.parse(await readFile(join(dir, file), 'utf8'))
  if (fixture && !Array.isArray(fixture) && fixture.expect_verdict && fixture.input) fixtures.push(fixture)
}
let failed = 0

for (const fixture of fixtures) {
  try {
    const verdict = await review(fixture.input)
    const errors = [...validate(verdict), ...toneErrors(verdict.applicant_message ?? '')]
    const matched = verdict.verdict === fixture.expect_verdict
    if (errors.length || !matched) failed++
    console.log(`\n${matched && !errors.length ? 'PASS' : 'FAIL'}  ${fixture.name}`)
    console.log(`  expected: ${fixture.expect_verdict}   got: ${verdict.verdict} (confidence ${verdict.confidence})`)
    if (errors.length) console.log(`  schema errors: ${errors.join('; ')}`)
    console.log(`  reasoning: ${verdict.reasoning}`)
    console.log(`  to applicant: ${verdict.applicant_message}`)
  } catch (error) {
    failed++
    console.log(`\nERROR ${fixture.name}: ${error.message}`)
  }
}

console.log(`\n${fixtures.length - failed}/${fixtures.length} fixtures passed`)
process.exit(failed ? 1 : 0)
