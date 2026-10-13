// Husky remains responsible for commit-msg and every other hook dispatcher.
// PR CI owns pre-push validation; remove the dispatcher this installation creates.
import husky from 'husky'
import { rmSync } from 'node:fs'

if (process.env.HUSKY !== '0') {
  const message = husky()
  if (message) throw new Error(`${message}; run hook setup from a Git checkout`)
  rmSync('.husky/_/pre-push')
}
