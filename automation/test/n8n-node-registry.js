#!/usr/bin/env node
// Prints the node types a running n8n build registers, as
// {"<node type>": [<typeVersion>, ...]} on stdout. Runs inside the n8n
// container, started by automation/test/n8n-import-check.sh.
//
// n8n serves the same list over HTTP at /types/nodes.json, behind an owner
// account and a session cookie. The packaged file needs neither, so the check
// works on a throwaway instance nobody has signed into.
'use strict';

const fs = require('fs');
const path = require('path');

// Both packages ship with the n8n image. A missing one throws here rather than
// dropping every node type it owns from the registry and passing the check.
const N8N_ROOT = '/usr/local/lib/node_modules/n8n';
const PACKAGES = ['n8n-nodes-base', '@n8n/n8n-nodes-langchain'];

const registry = {};

for (const pkg of PACKAGES) {
  const manifest = require.resolve(`${pkg}/package.json`, { paths: [N8N_ROOT] });
  const file = path.join(path.dirname(manifest), 'dist', 'types', 'nodes.json');
  for (const node of JSON.parse(fs.readFileSync(file, 'utf8'))) {
    // A versioned node is two entries under one name, one per version bundle,
    // so collect the versions instead of letting the later entry win.
    const key = `${pkg}.${node.name}`;
    const versions = Array.isArray(node.version) ? node.version : [node.version];
    registry[key] = (registry[key] || []).concat(versions);
  }
}

process.stdout.write(JSON.stringify(registry));
