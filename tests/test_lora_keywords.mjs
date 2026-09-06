import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from '../ui/node_modules/typescript/lib/typescript.js'

const source = readFileSync(new URL('../ui/src/lib/loraKeywords.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } })
const { appendLoraKeywords: append } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
assert.equal(append('A WATERCOLOR forest', ['watercolor', 'newStyle', 'newStyle']), 'A WATERCOLOR forest, newStyle')
assert.equal(append('cartoon', ['art']), 'cartoon, art')
assert.equal(append('a (style+)', ['(style+)', '[detail]']), 'a (style+), [detail]')
assert.equal(append('', [' exactTrigger ', '', 'exactTrigger']), 'exactTrigger')
assert.equal(append('日本語の絵', ['絵']), '日本語の絵, 絵')
console.log('LoRA keyword tests passed (5 cases)')
