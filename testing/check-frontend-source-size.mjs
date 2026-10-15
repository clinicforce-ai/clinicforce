import { readdir, readFile } from 'node:fs/promises'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const sourceRoot = join(dirname(fileURLToPath(import.meta.url)), '../frontend/src')
const limit = 500
const oversized = []
let checked = 0

async function visit(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) {
      await visit(path)
    } else if (/\.(css|ts|tsx)$/.test(entry.name)) {
      const source = await readFile(path, 'utf8')
      const lines = source.split(/\r?\n/).length - Number(source.endsWith('\n'))
      checked += 1
      if (lines > limit) oversized.push(`${relative(sourceRoot, path)}: ${lines} lines`)
    }
  }
}

await visit(sourceRoot)
if (oversized.length) {
  console.error(`Frontend source files exceed ${limit} lines:\n${oversized.join('\n')}`)
  process.exitCode = 1
} else {
  console.log(`${checked} frontend source files are within ${limit} lines.`)
}
